import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import RoleRequiredMixin
from config.constants import (
    ORDERS_EXTERNAL_READS_ENABLED,
    ORDERS_EXTERNAL_WRITES_ENABLED,
    ORDERS_GUIDE_MAX_BYTES,
    ORDERS_LOCAL_MODE,
)

from .functions.messaging import prepare_manual_followups
from .functions.querysets import operational_orders
from .functions.serializers import order_detail, order_row, shipment_dict, whatsapp_url
from .functions.supplier_responses import (
    SupplierResponseError,
    apply_supplier_novelty_category,
    apply_supplier_novelty_detail,
    apply_supplier_response,
)
from .models import (
    IntegrationStatus,
    LogisticsAudit,
    ManualFollowup,
    MessagingConfig,
    MessagingContact,
    Order,
    SavedFilter,
    Shipment,
    ShipmentDocument,
    WarehouseLocation,
)


OPERATOR_ROLES = ["Admin", "Operaciones", "Logistica", "Lider Comercial", "Gerencia"]
ALLOWED_GUIDE_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png"}
ALLOWED_GUIDE_SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png"}


def actor_name(request):
    return request.user.email or request.user.username


def safe_url(value):
    if not value:
        return True
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def parse_positive_int(value, default, maximum=200):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, 1), maximum)


class OrdersOverviewAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATOR_ROLES

    def get(self, request):
        orders = operational_orders()
        shipments = Shipment.objects.filter(order__in=orders)
        total = orders.count()
        without_guide = orders.filter(
            Q(shipments__isnull=True) | Q(shipments__tracking_number="")
        ).distinct().count()
        guide_without_tracking = orders.filter(
            shipments__tracking_number__gt="",
            shipments__logistics_state="guide_without_tracking",
        ).distinct().count()
        return Response(
            {
                "total": total,
                "without_guide": without_guide,
                "guide_without_tracking": guide_without_tracking,
                "in_transit": shipments.filter(logistics_state="in_transit").count(),
                "delivered": shipments.filter(logistics_state="delivered").count(),
                "exceptions": shipments.filter(
                    Q(incident_category__gt="") | Q(novelties__state="open")
                ).distinct().count(),
                "split": orders.annotate(shipment_total=Count("shipments")).filter(
                    shipment_total__gt=1
                ).count(),
                "sales_total": str(sum((item.grand_total for item in orders), 0)),
                "currency": "COP",
                "externalWrites": 0,
                "localMode": ORDERS_LOCAL_MODE,
            }
        )


class OrdersListAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATOR_ROLES

    def get(self, request):
        queryset = (
            operational_orders().prefetch_related(
                "items",
                "shipments__warehouse",
                "shipments__document",
            )
        )
        search = request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(visible_id__icontains=search)
                | Q(external_id__icontains=search)
                | Q(customer_name__icontains=search)
                | Q(customer_email__icontains=search)
                | Q(items__sku__icontains=search)
                | Q(shipments__tracking_number__icontains=search)
            )
        channel = request.query_params.get("channel", "").strip()
        if channel:
            if channel == "shopify":
                queryset = queryset.filter(channel="shopify").exclude(
                    source_snapshot__business_origin="sodimac"
                )
            elif channel == "sodimac":
                queryset = queryset.filter(
                    Q(channel="sodimac") | Q(source_snapshot__business_origin="sodimac")
                )
            else:
                queryset = queryset.filter(
                    Q(channel=channel) | Q(source_snapshot__business_origin=channel)
                )
        warehouse = request.query_params.get("warehouse", "").strip()
        if warehouse:
            queryset = queryset.filter(
                Q(shipments__warehouse__name__iexact=warehouse)
                | Q(shipments__warehouse_name__iexact=warehouse)
            )
        logistics_state = request.query_params.get("logistics_state", "").strip()
        if logistics_state:
            queryset = queryset.filter(shipments__logistics_state=logistics_state)
        from_date = request.query_params.get("from", "").strip()
        to_date = request.query_params.get("to", "").strip()
        if from_date:
            queryset = queryset.filter(placed_at__date__gte=from_date)
        if to_date:
            queryset = queryset.filter(placed_at__date__lte=to_date)

        guide_filter = request.query_params.get("guide", "").strip()
        if guide_filter == "missing":
            queryset = queryset.filter(
                Q(shipments__isnull=True) | Q(shipments__tracking_number="")
            )
        elif guide_filter == "present":
            queryset = queryset.filter(shipments__tracking_number__gt="")
        elif guide_filter == "present_without_tracking":
            queryset = queryset.filter(
                shipments__tracking_number__gt="",
                shipments__logistics_state="guide_without_tracking",
            )
        elif guide_filter == "missing_or_without_tracking":
            queryset = queryset.filter(
                Q(shipments__isnull=True)
                | Q(shipments__tracking_number="")
                | Q(
                    shipments__tracking_number__gt="",
                    shipments__logistics_state="guide_without_tracking",
                )
            )

        queryset = queryset.distinct()
        total = queryset.count()
        page = parse_positive_int(request.query_params.get("page"), 1)
        page_size = parse_positive_int(request.query_params.get("page_size"), 25, 100)
        start = (page - 1) * page_size
        rows = [order_row(order) for order in queryset[start : start + page_size]]
        return Response(
            {
                "orders": rows,
                "page": page,
                "pageSize": page_size,
                "total": total,
                "externalWrites": 0,
                "localMode": ORDERS_LOCAL_MODE,
            }
        )


class OrderDetailAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATOR_ROLES

    def get(self, request, order_id):
        order = (
            Order.objects.prefetch_related(
                "items",
                "shipments__warehouse",
                "shipments__document",
                "shipments__shipment_items__order_item",
                "shipments__tracking_events",
                "shipments__supplier_response_events",
                "shipments__novelties",
            )
            .filter(id=order_id)
            .first()
        )
        if not order:
            return Response({"detail": "Pedido no encontrado."}, status=404)
        return Response(order_detail(order))


class LocationsAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATOR_ROLES

    def get(self, request):
        return Response(
            {
                "locations": [
                    {
                        "id": str(location.id),
                        "external_id": location.external_id,
                        "name": location.name,
                        "reference": location.reference,
                    }
                    for location in WarehouseLocation.objects.filter(active=True)
                ]
            }
        )


class FilterOptionsAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATOR_ROLES

    def get(self, request):
        orders = operational_orders()
        channels = set(orders.values_list("channel", flat=True))
        channels.update(
            value
            for value in orders.values_list("source_snapshot__business_origin", flat=True)
            if value
        )
        return Response(
            {
                "warehouses": list(
                    WarehouseLocation.objects.filter(active=True).values_list("name", flat=True)
                ),
                "channels": sorted(channels),
                "carriers": list(
                    Shipment.objects.exclude(carrier="")
                    .order_by("carrier")
                    .values_list("carrier", flat=True)
                    .distinct()
                ),
            }
        )


class IntegrationsAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATOR_ROLES

    def get(self, request):
        statuses = {item.provider: item for item in IntegrationStatus.objects.all()}
        providers = []
        for provider in (
            "pamo_canonical",
            "shopify",
            "mercado_libre",
            "falabella",
            "sodimac",
            "envia",
        ):
            item = statuses.get(provider)
            providers.append(
                {
                    "provider": provider,
                    "state": item.state if item else "disabled_local",
                    "last_success_at": item.last_success_at.isoformat() if item and item.last_success_at else None,
                    "last_attempt_at": item.last_attempt_at.isoformat() if item and item.last_attempt_at else None,
                    "last_error_code": item.last_error_code if item else "",
                    "records_observed": item.records_observed if item else 0,
                    "details": item.details if item else {},
                }
            )
        return Response(
            {
                "providers": providers,
                "localMode": ORDERS_LOCAL_MODE,
                "externalReadsEnabled": ORDERS_EXTERNAL_READS_ENABLED,
                "externalWritesEnabled": ORDERS_EXTERNAL_WRITES_ENABLED,
                "externalWrites": 0,
            }
        )


class LocalSyncShieldAPI(RoleRequiredMixin, APIView):
    allowed_roles = ["Admin"]

    def post(self, request, provider):
        return Response(
            {
                "detail": "La sincronización externa está bloqueada en este entorno local.",
                "provider": provider,
                "externalWrites": 0,
                "externalReadsEnabled": ORDERS_EXTERNAL_READS_ENABLED,
            },
            status=status.HTTP_409_CONFLICT,
        )


class ShipmentAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATOR_ROLES

    @transaction.atomic
    def patch(self, request, shipment_id):
        shipment = (
            Shipment.objects.select_for_update()
            .select_related("warehouse", "order")
            .filter(id=shipment_id)
            .first()
        )
        if not shipment:
            return Response({"detail": "Despacho no encontrado."}, status=404)
        expected_version = request.data.get("version")
        if expected_version is not None and int(expected_version) != shipment.version:
            return Response(
                {"detail": "El despacho cambió. Recarga antes de volver a guardar.", "version": shipment.version},
                status=status.HTTP_409_CONFLICT,
            )
        if "tracking_url" in request.data and not safe_url(request.data.get("tracking_url")):
            return Response({"tracking_url": ["La URL debe usar http o https."]}, status=400)

        actor = actor_name(request)
        editable = {
            "carrier",
            "tracking_number",
            "tracking_url",
            "logistics_state",
            "customer_context",
        }
        changed = []
        for field in editable:
            if field not in request.data:
                continue
            new_value = request.data.get(field) or ""
            old_value = getattr(shipment, field)
            if str(old_value or "") == str(new_value):
                continue
            setattr(shipment, field, new_value)
            LogisticsAudit.objects.create(
                shipment=shipment,
                field=field,
                previous_value=str(old_value or ""),
                new_value=str(new_value),
                actor=actor,
                source="manual",
            )
            changed.append(field)

        if "warehouse_location_id" in request.data:
            warehouse_id = request.data.get("warehouse_location_id")
            warehouse = WarehouseLocation.objects.filter(id=warehouse_id, active=True).first()
            if not warehouse:
                return Response({"warehouse_location_id": ["Bodega inválida."]}, status=400)
            old_value = shipment.effective_warehouse_name
            if shipment.warehouse_id != warehouse.id:
                shipment.warehouse = warehouse
                shipment.warehouse_name = warehouse.name
                shipment.warehouse_reference = warehouse.reference
                shipment.warehouse_locked = True
                shipment.warehouse_assignment_source = "manual"
                LogisticsAudit.objects.create(
                    shipment=shipment,
                    field="warehouse",
                    previous_value=old_value,
                    new_value=warehouse.name,
                    actor=actor,
                    source="manual",
                    detail="Asignación manual protegida contra sincronización.",
                )
                changed.append("warehouse")

        if changed:
            shipment.version += 1
            shipment.save()
        return Response({"shipment": shipment_dict(shipment, detailed=True), "changed": changed})


class ShipmentIncidentAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATOR_ROLES

    @transaction.atomic
    def patch(self, request, shipment_id):
        shipment = Shipment.objects.select_for_update().filter(id=shipment_id).first()
        if not shipment:
            return Response({"detail": "Despacho no encontrado."}, status=404)
        category = (request.data.get("category") or "").strip()
        detail = (request.data.get("detail") or "").strip()
        if category == "custom" and not detail:
            return Response({"detail": ["La novedad personalizada requiere detalle."]}, status=400)
        previous = f"{shipment.incident_category}|{shipment.incident_detail}"
        shipment.incident_category = category
        shipment.incident_detail = detail
        if category:
            shipment.logistics_state = "exception"
        shipment.version += 1
        shipment.save()
        LogisticsAudit.objects.create(
            shipment=shipment,
            field="incident",
            previous_value=previous,
            new_value=f"{category}|{detail}",
            actor=actor_name(request),
            source="manual",
        )
        return Response({"shipment": shipment_dict(shipment, detailed=True)})


class SupplierResponseSimulationAPI(RoleRequiredMixin, APIView):
    allowed_roles = [*OPERATOR_ROLES, "Integraciones"]

    def post(self, request, shipment_id):
        if not (settings.DEBUG and ORDERS_LOCAL_MODE):
            return Response({"detail": "Simulacion disponible solo en local."}, status=404)
        action = str(request.data.get("action") or "").strip()
        category = str(request.data.get("category") or "").strip()
        detail = str(request.data.get("detail") or "")
        event_id = str(request.data.get("event_id") or "").strip()
        if not event_id or len(event_id) > 180:
            return Response({"event_id": ["Identificador requerido."]}, status=400)
        try:
            if action == "classify_issue":
                result = apply_supplier_novelty_category(
                    shipment_id=shipment_id,
                    category=category,
                    provider_event_id=event_id,
                    sender_phone="local-simulator",
                    source="local_simulator",
                    validate_contact=False,
                )
            elif action == "provide_issue_detail":
                result = apply_supplier_novelty_detail(
                    shipment_id=shipment_id,
                    detail=detail,
                    provider_event_id=event_id,
                    sender_phone="local-simulator",
                    source="local_simulator",
                    validate_contact=False,
                )
            else:
                result = apply_supplier_response(
                    shipment_id=shipment_id,
                    action=action,
                    provider_event_id=event_id,
                    sender_phone="local-simulator",
                    source="local_simulator",
                    validate_contact=False,
                )
        except SupplierResponseError as error:
            return Response({"detail": error.code}, status=error.http_status)
        return Response({"supplierResponse": result, "externalWrites": 0})


class ShipmentDocumentAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATOR_ROLES
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request, shipment_id):
        document = ShipmentDocument.objects.filter(shipment_id=shipment_id).first()
        if not document:
            return Response({"detail": "Este despacho no tiene una guía cargada."}, status=404)
        response = FileResponse(
            document.file.open("rb"),
            content_type=document.mime_type,
            filename=document.original_name,
        )
        response["Content-Disposition"] = f'inline; filename="{document.original_name}"'
        response["Cache-Control"] = "private, no-store"
        return response

    @transaction.atomic
    def post(self, request, shipment_id):
        shipment = Shipment.objects.select_for_update().filter(id=shipment_id).first()
        if not shipment:
            return Response({"detail": "Despacho no encontrado."}, status=404)
        uploaded = request.FILES.get("file")
        if not uploaded:
            return Response({"file": ["Selecciona un archivo."]}, status=400)
        suffix = Path(uploaded.name).suffix.lower()
        if uploaded.content_type not in ALLOWED_GUIDE_MIME_TYPES or suffix not in ALLOWED_GUIDE_SUFFIXES:
            return Response({"file": ["Solo se permiten PDF, JPG y PNG."]}, status=400)
        if uploaded.size > ORDERS_GUIDE_MAX_BYTES:
            return Response({"file": ["La guía supera el tamaño máximo permitido."]}, status=400)
        digest = hashlib.sha256()
        for chunk in uploaded.chunks():
            digest.update(chunk)
        uploaded.seek(0)

        previous = ShipmentDocument.objects.filter(shipment=shipment).first()
        if previous:
            previous.file.delete(save=False)
            previous.delete()
        document = ShipmentDocument.objects.create(
            shipment=shipment,
            file=uploaded,
            original_name=Path(uploaded.name).name,
            mime_type=uploaded.content_type,
            size_bytes=uploaded.size,
            sha256=digest.hexdigest(),
            uploaded_by=actor_name(request),
        )
        LogisticsAudit.objects.create(
            shipment=shipment,
            field="document",
            previous_value="present" if previous else "",
            new_value=document.original_name,
            actor=actor_name(request),
            source="manual",
            detail="Documento privado; acceso autenticado.",
        )
        if shipment.guide_delivery_state == "requested":
            shipment.guide_delivery_state = "ready_to_send"
        shipment.version += 1
        shipment.save(update_fields=["guide_delivery_state", "version", "updated_at"])
        return Response(
            {
                "document": {
                    "name": document.original_name,
                    "mime_type": document.mime_type,
                    "size_bytes": document.size_bytes,
                    "url": f"/api/pedidos/shipments/{shipment.id}/document/",
                },
                "externalWrites": 0,
            },
            status=201,
        )


class MessagingConfigsAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATOR_ROLES

    def get(self, request):
        configs = MessagingConfig.objects.select_related("warehouse").prefetch_related("contacts")
        return Response({"configs": [self._serialize(item) for item in configs]})

    @transaction.atomic
    def put(self, request):
        warehouse = WarehouseLocation.objects.filter(id=request.data.get("warehouse_id"), active=True).first()
        if not warehouse:
            return Response({"warehouse_id": ["Bodega inválida."]}, status=400)
        config, _ = MessagingConfig.objects.get_or_create(warehouse=warehouse)
        config.template_body = request.data.get("template_body") or config.template_body
        config.followup_template_body = request.data.get("followup_template_body") or ""
        try:
            maximum_attempts = int(request.data.get("maximum_attempts", 2))
        except (TypeError, ValueError):
            transaction.set_rollback(True)
            return Response({"maximum_attempts": ["Debe ser un número entre 1 y 2."]}, status=400)
        config.maximum_attempts = min(max(maximum_attempts, 1), 2)
        config.active = bool(request.data.get("active", True))
        config.updated_by = actor_name(request)
        config.save()
        contacts = request.data.get("contacts", [])
        if not isinstance(contacts, list):
            return Response({"contacts": ["Formato inválido."]}, status=400)
        normalized_contacts = []
        seen_phones = set()
        for contact in contacts:
            name = str(contact.get("name", "")).strip()
            phone = "".join(character for character in str(contact.get("phone", "")) if character.isdigit())
            if not name or not 8 <= len(phone) <= 15:
                transaction.set_rollback(True)
                return Response({"contacts": ["Cada contacto necesita nombre y teléfono válido."]}, status=400)
            if phone in seen_phones:
                transaction.set_rollback(True)
                return Response({"contacts": ["El mismo teléfono no puede repetirse en una bodega."]}, status=400)
            seen_phones.add(phone)
            normalized_contacts.append(
                {
                    "id": contact.get("id"),
                    "name": name,
                    "phone": phone,
                    "active": bool(contact.get("active", True)),
                }
            )
        retained_ids = []
        for contact in normalized_contacts:
            contact_id = contact.pop("id", None)
            if contact_id:
                item = config.contacts.filter(id=contact_id).first()
                if not item:
                    transaction.set_rollback(True)
                    return Response({"contacts": ["Contacto inválido para esta bodega."]}, status=400)
                for field, value in contact.items():
                    setattr(item, field, value)
                item.save(update_fields=["name", "phone", "active"])
            else:
                item = MessagingContact.objects.create(config=config, **contact)
            retained_ids.append(item.id)
        config.contacts.exclude(id__in=retained_ids).delete()
        return Response({"config": self._serialize(config)})

    @staticmethod
    def _serialize(config):
        return {
            "id": config.id,
            "warehouse_id": config.warehouse_id,
            "warehouse_name": config.warehouse.name,
            "template_body": config.template_body,
            "followup_template_body": config.followup_template_body,
            "maximum_attempts": config.maximum_attempts,
            "active": config.active,
            "contacts": [
                {"id": item.id, "name": item.name, "phone": item.phone, "active": item.active}
                for item in config.contacts.all()
            ],
        }


class ManualMessagingAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATOR_ROLES

    def post(self, request):
        shipment_ids = request.data.get("shipment_ids", [])
        if not isinstance(shipment_ids, list) or not shipment_ids:
            return Response({"shipment_ids": ["Selecciona al menos un despacho."]}, status=400)
        generated, missing_config, skipped = prepare_manual_followups(
            shipment_ids=shipment_ids,
            actor=actor_name(request),
        )
        if not generated:
            return Response(
                {
                    "detail": "No hay una configuración activa con contacto para las bodegas seleccionadas.",
                    "missing_config": missing_config,
                    "skipped": skipped,
                },
                status=400,
            )
        return Response(
            {
                "generated": generated,
                "recipientCount": len(generated),
                "shipmentCount": len(
                    {item["shipmentId"] for item in generated if item["shipmentId"]}
                ),
                "missing_config": missing_config,
                "skipped": skipped,
                "externalWrites": 0,
                "detail": "Mensajes preparados. Nada se envió automáticamente.",
            },
            status=201,
        )


class ManualMessagingDetailAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATOR_ROLES

    def post(self, request, followup_id, action):
        followup = ManualFollowup.objects.filter(id=followup_id).first()
        if not followup:
            return Response({"detail": "Preparación no encontrada."}, status=404)
        if action == "open":
            followup.opened_at = timezone.now()
            followup.state = "manual_opened"
            followup.save(update_fields=["opened_at", "state"])
        elif action == "confirm":
            followup.confirmed_at = timezone.now()
            followup.state = "manual_confirmed"
            followup.save(update_fields=["confirmed_at", "state"])
        else:
            return Response({"detail": "Acción inválida."}, status=400)
        return Response(
            {
                "id": str(followup.id),
                "state": followup.state,
                "whatsappUrl": whatsapp_url(followup.phone, followup.rendered_message),
                "externalWrites": 0,
            }
        )


class SavedFiltersAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATOR_ROLES

    def get(self, request):
        return Response(
            {
                "filters": [
                    {
                        "id": item.id,
                        "name": item.name,
                        "filters": item.filters,
                        "created_at": item.created_at.isoformat(),
                        "updated_at": item.updated_at.isoformat(),
                    }
                    for item in SavedFilter.objects.filter(owner=request.user)
                ]
            }
        )

    def post(self, request):
        name = str(request.data.get("name", "")).strip()
        filters = request.data.get("filters", [])
        if not name or not isinstance(filters, list):
            return Response({"detail": "Nombre y filtros son obligatorios."}, status=400)
        item, created = SavedFilter.objects.update_or_create(
            owner=request.user,
            name=name,
            defaults={"filters": filters},
        )
        return Response({"id": item.id, "name": item.name, "filters": item.filters}, status=201 if created else 200)


class SavedFilterDetailAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATOR_ROLES

    def delete(self, request, filter_id):
        deleted, _ = SavedFilter.objects.filter(id=filter_id, owner=request.user).delete()
        if not deleted:
            return Response({"detail": "Filtro no encontrado."}, status=404)
        return Response(status=204)
