from datetime import date
import json
from urllib.parse import quote, urlparse

from django.core.cache import cache
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Prefetch, Q
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from accounts.permissions import RoleRequiredMixin
from config.constants import (
    CORS_ALLOWED_ORIGINS,
    REMITTANCE_SUPPLIER_INVOICE_DAILY_LIMIT,
    SIIGO_ACCESS_KEY,
    SIIGO_LIVE_READS_ENABLED,
    SIIGO_PARTNER_ID,
    SIIGO_USERNAME,
)
from integrations.openai_supplier_invoice import OpenAISupplierInvoiceReader
from .functions.siigo_invoice import (
    SiigoCredentials,
    SiigoPreflightError,
    SiigoReadClient,
)
from .functions.confirm_remittance import confirm_remittance
from .functions.recipient_completion import (
    RecipientCompletionError,
    accept_recipient_completion,
    find_share,
    prepare_recipient_link,
    public_recipient_form,
)
from .functions.remittance_document import build_remittance_pdf
from .functions.remittance_domain import (
    RemittanceDomainError,
    calculate_margin_price,
    calculate_siigo_invoice_price,
    calculate_supplier_commercials,
)
from .functions.supplier_invoice import SupplierInvoiceError, validate_supplier_invoice
from .models import (
    AuthorizedPerson,
    Remittance,
    RemittanceAuditEvent,
    RemittanceFavorite,
    RemittanceLine,
    RemittanceParty,
    RemittanceSupplierInvoiceFile,
    RemittanceUsageDestination,
    RemittanceWarehouse,
)
from .serializers import (
    AccountingCommercialPreparationSerializer,
    AccountingPrivateAdjustmentsSerializer,
    AuthorizedPersonSerializer,
    FavoriteSerializer,
    PartySerializer,
    RemittanceSerializer,
    RemittanceSupplierInvoiceFileSerializer,
    UsageDestinationSerializer,
    WarehouseSerializer,
)


OPERATIONS_ROLES = ["Admin", "Operaciones", "Logistica"]
ACCOUNTING_ROLES = ["Admin", "Facturacion"]
SHARE_ROLES = ["Admin", "Operaciones", "Logistica", "Facturacion"]


def remittance_queryset():
    return Remittance.objects.select_related("warehouse", "supplier", "customer", "delivery").prefetch_related(
        Prefetch("lines", queryset=RemittanceLine.objects.order_by("line_number")),
        "supplier_invoice_files",
        "recipient_acceptance",
    )


def recipient_error(error):
    return Response({"detail": error.detail, "code": error.code}, status=error.status_code)


def trusted_public_base(raw_value):
    value = str(raw_value or "").rstrip("/")
    parsed = urlparse(value)
    if parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
        return None
    allowed = {origin.rstrip("/") for origin in CORS_ALLOWED_ORIGINS}
    if value not in allowed:
        return None
    if parsed.scheme == "https":
        return value
    if parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}:
        return value
    return None


def serialized(remittance_or_queryset, request, *, many=False, accounting=False):
    include_accounting = accounting or request.user.is_superuser or request.user.groups.filter(name="Facturacion").exists()
    return RemittanceSerializer(
        remittance_or_queryset,
        many=many,
        context={"request": request, "include_accounting": include_accounting},
    ).data


class RemittanceListCreateAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATIONS_ROLES

    def get(self, request):
        queryset = remittance_queryset()
        status_filter = request.query_params.get("invoice_status")
        if status_filter:
            queryset = queryset.filter(invoice_status=status_filter)
        return Response(serialized(queryset[:200], request, many=True))

    def post(self, request):
        serializer = RemittanceSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        remittance = serializer.save()
        return Response(serialized(remittance_queryset().get(pk=remittance.pk), request), status=201)


class RemittanceDetailAPI(RoleRequiredMixin, APIView):
    allowed_roles = ["Admin", "Operaciones", "Logistica", "Facturacion"]

    def get(self, request, remittance_id):
        remittance = get_object_or_404(remittance_queryset(), pk=remittance_id)
        return Response(serialized(remittance, request))


class RemittanceConfirmAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATIONS_ROLES

    def post(self, request, remittance_id):
        expected_version = request.data.get("expected_version")
        if not isinstance(expected_version, int):
            return Response({"expected_version": "Envía la versión esperada."}, status=400)
        remittance = confirm_remittance(remittance_id, expected_version, request.user)
        return Response(serialized(remittance_queryset().get(pk=remittance.pk), request))


class RemittancePrepareWhatsAppAPI(RoleRequiredMixin, APIView):
    allowed_roles = SHARE_ROLES

    def post(self, request, remittance_id):
        remittance = get_object_or_404(remittance_queryset(), pk=remittance_id)
        public_base = trusted_public_base(request.data.get("public_base_url"))
        if not public_base:
            return Response({
                "detail": "El origen público no está autorizado.",
                "code": "UNTRUSTED_PUBLIC_ORIGIN",
            }, status=400)
        try:
            share, token = prepare_recipient_link(remittance, request.user)
        except RecipientCompletionError as error:
            return recipient_error(error)
        public_url = f"{public_base}/remisiones/firmar/{token}"
        message = (
            f"Remisión {remittance.number} de PAMO. "
            f"Revisa la mercancía, asigna el destino de uso y firma aquí: {public_url}"
        )
        return Response({
            "public_url": public_url,
            "whatsapp_url": f"https://wa.me/?text={quote(message)}",
            "expires_at": share.expires_at,
        })


class PublicRemittanceRecipientAPI(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, token):
        try:
            return Response(public_recipient_form(token))
        except RecipientCompletionError as error:
            return recipient_error(error)

    def post(self, request, token):
        try:
            acceptance, created = accept_recipient_completion(token, request.data)
        except RecipientCompletionError as error:
            return recipient_error(error)
        return Response({
            "status": "SIGNED",
            "signerName": acceptance.signer_name,
            "signedAt": acceptance.signed_at,
        }, status=201 if created else 200)


def remittance_pdf_response(remittance, request):
    filename = f"{remittance.number or 'remision'}.pdf"
    disposition = "attachment" if request.query_params.get("download") == "1" else "inline"
    response = HttpResponse(build_remittance_pdf(remittance), content_type="application/pdf")
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


class RemittanceDocumentAPI(RoleRequiredMixin, APIView):
    allowed_roles = SHARE_ROLES

    def get(self, request, remittance_id):
        remittance = get_object_or_404(remittance_queryset(), pk=remittance_id)
        return remittance_pdf_response(remittance, request)


class PublicRemittanceDocumentAPI(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, token):
        try:
            share = find_share(token)
        except RecipientCompletionError as error:
            return recipient_error(error)
        remittance = get_object_or_404(remittance_queryset(), pk=share.remittance_id)
        return remittance_pdf_response(remittance, request)


class RemittanceReferenceDataAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATIONS_ROLES

    def get(self, request):
        favorites = RemittanceFavorite.objects.filter(is_active=True).select_related("party")
        warehouses = RemittanceWarehouse.objects.filter(is_active=True)
        authorized_people = AuthorizedPerson.objects.filter(
            is_active=True,
            customer__party_type="CUSTOMER",
        ).select_related("customer")
        usage_destinations = RemittanceUsageDestination.objects.filter(
            is_active=True,
            customer__party_type="CUSTOMER",
        ).select_related("customer")

        people_data = list(AuthorizedPersonSerializer(authorized_people, many=True).data)
        known_people = {(item["customer"], item["name"]) for item in people_data}
        historical_people = Remittance.objects.exclude(requester_name="").values(
            "customer_id", "requester_name", "requester_document"
        ).order_by("customer_id", "requester_name", "-created_at")
        for item in historical_people:
            key = (item["customer_id"], item["requester_name"])
            if key in known_people:
                continue
            people_data.append({
                "id": f"history-person-{item['customer_id']}-{len(people_data)}",
                "customer": item["customer_id"],
                "name": item["requester_name"],
                "document": item["requester_document"],
                "phone": "",
            })
            known_people.add(key)

        destinations_data = list(UsageDestinationSerializer(usage_destinations, many=True).data)
        known_destinations = {(item["customer"], item["value"]) for item in destinations_data}
        historical_destinations = RemittanceLine.objects.exclude(usage_destination="").values(
            "remittance__customer_id", "usage_destination"
        ).order_by("remittance__customer_id", "usage_destination")
        for item in historical_destinations:
            customer_id = item["remittance__customer_id"]
            value = item["usage_destination"]
            key = (customer_id, value)
            if key in known_destinations:
                continue
            destinations_data.append({
                "id": f"history-destination-{customer_id}-{len(destinations_data)}",
                "customer": customer_id,
                "value": value,
            })
            known_destinations.add(key)

        return Response({
            "warehouses": WarehouseSerializer(warehouses, many=True).data,
            "favorites": FavoriteSerializer(favorites, many=True).data,
            "authorized_people": people_data,
            "usage_destinations": destinations_data,
        })


def _siigo_party_name(item):
    raw_name = item.get("name") or item.get("commercial_name") or ""
    if isinstance(raw_name, list):
        raw_name = " ".join(str(part) for part in raw_name if part)
    return " ".join(str(raw_name).split()).upper()


def _exact_siigo_supplier(nit):
    client = SiigoReadClient(SiigoCredentials(
        username=SIIGO_USERNAME,
        access_key=SIIGO_ACCESS_KEY,
        partner_id=SIIGO_PARTNER_ID,
    ))
    payload = client.get("/v1/customers", params={
        "identification": nit,
        "branch_office": 0,
        "active": "true",
        "type": "Supplier",
    })
    matches = [item for item in payload.get("results", []) if (
        item.get("active") is not False
        and "".join(character for character in str(item.get("identification") or "") if character.isdigit()) == nit
        and int(item.get("branch_office") or 0) == 0
        and str(item.get("type") or "Supplier").strip().lower() == "supplier"
    )]
    if len(matches) != 1:
        return None
    item = matches[0]
    return {
        "siigo_id": str(item.get("id") or ""),
        "nit": nit,
        "name": _siigo_party_name(item),
    }


class RemittanceSupplierSearchAPI(RoleRequiredMixin, APIView):
    """Busca proveedores sin crear ni modificar terceros en Siigo."""

    allowed_roles = OPERATIONS_ROLES

    def get(self, request):
        query = " ".join(str(request.query_params.get("q") or "").split())
        if len(query) < 2:
            return Response({"detail": "Escribe al menos 2 caracteres para buscar."}, status=400)

        local = list(
            RemittanceParty.objects.filter(party_type=RemittanceParty.PartyType.SUPPLIER)
            .filter(Q(name__icontains=query) | Q(nit__icontains=query))[:25]
        )
        results = [{
            **PartySerializer(item).data,
            "source": "LOCAL_VALIDATED" if item.is_validated else "LOCAL_HISTORY",
            "requires_import": False,
        } for item in local]

        normalized_nit = "".join(character for character in query if character.isdigit())
        live_lookup = "NOT_REQUESTED"
        if len(normalized_nit) >= 6 and not any(item.nit == normalized_nit for item in local):
            if not SIIGO_LIVE_READS_ENABLED:
                live_lookup = "DISABLED"
            else:
                try:
                    supplier = _exact_siigo_supplier(normalized_nit)
                except SiigoPreflightError as error:
                    return Response({
                        "detail": error.detail,
                        "code": error.code,
                        "results": results,
                    }, status=503)
                live_lookup = "MATCH" if supplier else "NOT_FOUND"
                if supplier:
                    results.append({
                        "id": None,
                        "party_type": RemittanceParty.PartyType.SUPPLIER,
                        "is_validated": True,
                        "source": "SIIGO_LIVE_READ",
                        "requires_import": True,
                        **supplier,
                    })

        return Response({
            "source": "LOCAL_AND_EXACT_SIIGO_NIT",
            "external_write": False,
            "live_lookup": live_lookup,
            "results": results,
            "hint": "La API de Siigo permite consulta exacta por NIT. Por nombre se buscan proveedores ya guardados localmente.",
        })

    def post(self, request):
        nit = "".join(character for character in str(request.data.get("nit") or "") if character.isdigit())
        if len(nit) < 6:
            return Response({"nit": "Envía un NIT válido para confirmar el proveedor."}, status=400)
        if not SIIGO_LIVE_READS_ENABLED:
            return Response({
                "detail": "La consulta en vivo de Siigo está desactivada en este proceso local.",
                "code": "SIIGO_LIVE_READS_DISABLED",
            }, status=409)
        try:
            supplier = _exact_siigo_supplier(nit)
        except SiigoPreflightError as error:
            return Response({"detail": error.detail, "code": error.code}, status=503)
        if not supplier or not supplier["name"] or not supplier["siigo_id"]:
            return Response({
                "detail": "No se encontró un proveedor principal activo con ese NIT en Siigo.",
                "code": "SIIGO_SUPPLIER_NOT_FOUND",
            }, status=404)

        party, _ = RemittanceParty.objects.update_or_create(
            party_type=RemittanceParty.PartyType.SUPPLIER,
            nit=nit,
            defaults={
                "name": supplier["name"],
                "siigo_id": supplier["siigo_id"],
                "is_validated": True,
            },
        )
        return Response({
            **PartySerializer(party).data,
            "source": "SIIGO_LIVE_READ",
            "external_write": False,
        })


class SupplierInvoiceInterpretAPI(RoleRequiredMixin, APIView):
    """Interpreta un adjunto privado sin persistirlo todavía."""

    allowed_roles = OPERATIONS_ROLES
    reader_class = OpenAISupplierInvoiceReader

    def post(self, request):
        uploaded_file = request.FILES.get("file")
        if not uploaded_file:
            return Response({"detail": "Selecciona una factura.", "code": "FILE_REQUIRED"}, status=400)

        try:
            invoice = validate_supplier_invoice(uploaded_file)
            reader = self.reader_class()
            local_reader = getattr(type(reader), "read_local", None)
            local_data = local_reader(reader, invoice) if callable(local_reader) else None
            if local_data:
                return Response({
                    "data": local_data,
                    "file": {
                        "original_name": invoice.original_name,
                        "mime_type": invoice.mime_type,
                        "size_bytes": len(invoice.body),
                        "sha256": invoice.sha256,
                    },
                })

            cache_key = f"supplier-invoice-ai:{date.today().isoformat()}:{request.user.pk}"
            attempts = cache.get(cache_key, 0)
            if attempts >= REMITTANCE_SUPPLIER_INVOICE_DAILY_LIMIT:
                return Response({
                    "detail": "Se alcanzó el límite diario de lecturas. Puedes continuar en captura manual.",
                    "code": "AI_DAILY_LIMIT_REACHED",
                }, status=429)
            cache.set(cache_key, attempts + 1, timeout=60 * 60 * 26)
            data = reader.read(invoice)
        except SupplierInvoiceError as error:
            return Response({"detail": str(error), "code": error.code}, status=error.status_code)

        return Response({
            "data": data,
            "file": {
                "original_name": invoice.original_name,
                "mime_type": invoice.mime_type,
                "size_bytes": len(invoice.body),
                "sha256": invoice.sha256,
            },
        })


class SupplierInvoiceUploadAPI(RoleRequiredMixin, APIView):
    """Persiste un adjunto contable privado sobre un borrador existente."""

    allowed_roles = OPERATIONS_ROLES

    def post(self, request, remittance_id):
        remittance = get_object_or_404(Remittance, pk=remittance_id)
        if remittance.document_status != Remittance.DocumentStatus.DRAFT:
            return Response({
                "detail": "La factura del proveedor solo puede adjuntarse mientras la remisión es borrador.",
                "code": "REMITTANCE_NOT_DRAFT",
            }, status=409)
        uploaded_file = request.FILES.get("file")
        if not uploaded_file:
            return Response({"detail": "Selecciona una factura.", "code": "FILE_REQUIRED"}, status=400)

        try:
            invoice = validate_supplier_invoice(uploaded_file)
        except SupplierInvoiceError as error:
            return Response({"detail": str(error), "code": error.code}, status=error.status_code)

        existing = RemittanceSupplierInvoiceFile.objects.filter(
            remittance=remittance,
            sha256=invoice.sha256,
        ).first()
        if existing:
            return Response(RemittanceSupplierInvoiceFileSerializer(existing).data)

        try:
            with transaction.atomic():
                stored = RemittanceSupplierInvoiceFile(
                    remittance=remittance,
                    original_name=invoice.original_name,
                    mime_type=invoice.mime_type,
                    size_bytes=len(invoice.body),
                    sha256=invoice.sha256,
                    uploaded_by=request.user,
                )
                stored.file.save(invoice.original_name, ContentFile(invoice.body), save=False)
                stored.save()
                RemittanceAuditEvent.objects.create(
                    remittance=remittance,
                    event_type="SUPPLIER_INVOICE_ATTACHED",
                    actor=request.user,
                    details={
                        "file_id": str(stored.id),
                        "mime_type": stored.mime_type,
                        "size_bytes": stored.size_bytes,
                        "sha256": stored.sha256,
                    },
                )
        except IntegrityError:
            stored = RemittanceSupplierInvoiceFile.objects.get(
                remittance=remittance,
                sha256=invoice.sha256,
            )
        return Response(RemittanceSupplierInvoiceFileSerializer(stored).data, status=201)


class SupplierInvoiceDownloadAPI(RoleRequiredMixin, APIView):
    """Descarga autenticada; el archivo nunca se expone mediante MEDIA_URL."""

    allowed_roles = ACCOUNTING_ROLES

    def get(self, request, remittance_id, file_id):
        stored = get_object_or_404(
            RemittanceSupplierInvoiceFile,
            pk=file_id,
            remittance_id=remittance_id,
        )
        response = FileResponse(
            stored.file.open("rb"),
            as_attachment=True,
            filename=stored.original_name,
            content_type=stored.mime_type,
        )
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class RemittanceAccountingQueueAPI(RoleRequiredMixin, APIView):
    allowed_roles = ACCOUNTING_ROLES

    def get(self, request):
        queryset = remittance_queryset().exclude(document_status=Remittance.DocumentStatus.CANCELLED)
        return Response(serialized(queryset[:200], request, many=True, accounting=True))


class RemittanceSiigoProductSearchAPI(RoleRequiredMixin, APIView):
    """Busca únicamente en el último catálogo Siigo persistido en modo lectura."""

    allowed_roles = ACCOUNTING_ROLES

    def get(self, request):
        query = str(request.query_params.get("q") or "").strip()
        if len(query) < 2:
            return Response({"detail": "Escribe al menos 2 caracteres para buscar."}, status=400)
        candidates = list(
            SiigoProductSnapshot.objects.filter(active=True)
            .filter(Q(sku__icontains=query) | Q(name__icontains=query))
            .only(
                "siigo_id", "sku", "name", "sale_price", "tax_rate",
                "tax_included", "observed_at", "evidence_reference",
            )[:50]
        )
        normalized = query.upper()
        candidates.sort(key=lambda item: (item.sku.upper() != normalized, item.sku.upper(), item.name.upper()))
        return Response({
            "source": "LOCAL_SIIGO_SNAPSHOT",
            "external_read": False,
            "results": [{
                "siigo_id": item.siigo_id,
                "sku": item.sku,
                "name": item.name,
                "sale_price": item.sale_price,
                "tax_rate": item.tax_rate,
                "tax_included": item.tax_included,
                "observed_at": item.observed_at,
                "evidence_reference": item.evidence_reference,
            } for item in candidates[:25]],
        })


class RemittanceAccountingPrivateAdjustmentsAPI(RoleRequiredMixin, APIView):
    allowed_roles = ACCOUNTING_ROLES

    def patch(self, request, remittance_id):
        serializer = AccountingPrivateAdjustmentsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        with transaction.atomic():
            remittance = get_object_or_404(Remittance.objects.select_for_update(), pk=remittance_id)
            if data["expected_version"] != remittance.version:
                return Response({
                    "detail": "La remisión cambió mientras la revisabas. Actualiza la cola antes de guardar.",
                    "code": "VERSION_CONFLICT",
                }, status=409)
            if remittance.invoice_status in {
                Remittance.InvoiceStatus.INVOICING,
                Remittance.InvoiceStatus.INVOICED,
            }:
                return Response({
                    "detail": "Los ajustes privados no pueden cambiarse durante o después de la facturación.",
                    "code": "ACCOUNTING_LOCKED",
                }, status=409)

            global_fields = [
                "supplier_global_discount_percent",
                "supplier_global_discount_value",
                "supplier_other_charges",
                "supplier_freight_cost",
            ]
            changed_fields = []
            for field in global_fields:
                if field in data:
                    setattr(remittance, field, data[field])
                    changed_fields.append(field)

            lines_by_id = {
                line.id: line
                for line in remittance.lines.select_for_update().all()
            }
            private_line_fields = [
                "supplier_sku",
                "supplier_unit_cost",
                "supplier_line_total",
                "supplier_discount_percent",
                "supplier_discount_value",
            ]
            changed_line_ids = []
            for line_data in data["lines"]:
                line = lines_by_id.get(line_data["id"])
                if line is None:
                    return Response({
                        "detail": "Uno de los productos no pertenece a esta remisión.",
                        "code": "INVALID_LINE",
                    }, status=400)
                line_fields = []
                for field in private_line_fields:
                    if field in line_data:
                        value = line_data[field]
                        if field == "supplier_sku":
                            value = value.strip().upper()
                        setattr(line, field, value)
                        line_fields.append(field)
                if line_fields:
                    line.save(update_fields=line_fields)
                    changed_line_ids.append(line.id)

            remittance.version += 1
            remittance.save(update_fields=[*changed_fields, "version", "updated_at"])
            RemittanceAuditEvent.objects.create(
                remittance=remittance,
                event_type="ACCOUNTING_PRIVATE_ADJUSTMENTS_UPDATED",
                actor=request.user,
                details={
                    "global_fields": changed_fields,
                    "line_ids": changed_line_ids,
                },
            )

        refreshed = remittance_queryset().get(pk=remittance.pk)
        return Response(serialized(refreshed, request, accounting=True))


class RemittanceAccountingCommercialPreparationAPI(RoleRequiredMixin, APIView):
    allowed_roles = ACCOUNTING_ROLES

    def patch(self, request, remittance_id):
        serializer = AccountingCommercialPreparationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            with transaction.atomic():
                remittance = get_object_or_404(Remittance.objects.select_for_update(), pk=remittance_id)
                if data["expected_version"] != remittance.version:
                    return Response({
                        "detail": "La remisión cambió mientras la revisabas. Actualiza la cola antes de guardar.",
                        "code": "VERSION_CONFLICT",
                    }, status=409)
                if remittance.invoice_status in {
                    Remittance.InvoiceStatus.INVOICING,
                    Remittance.InvoiceStatus.INVOICED,
                }:
                    return Response({
                        "detail": "La preparación no puede cambiarse durante o después de la facturación.",
                        "code": "ACCOUNTING_LOCKED",
                    }, status=409)

                lines_by_id = {line.id: line for line in remittance.lines.select_for_update().all()}
                submitted_by_id = {line["id"]: line for line in data["lines"]}
                if set(submitted_by_id) != set(lines_by_id):
                    return Response({
                        "detail": "La preparación debe incluir todos los productos actuales de la remisión.",
                        "code": "INCOMPLETE_LINES",
                    }, status=400)

                requested_skus = {
                    str(line["siigo_sku"]).strip().upper()
                    for line in data["lines"] if str(line["siigo_sku"]).strip()
                }
                sku_filter = Q()
                for requested_sku in requested_skus:
                    sku_filter |= Q(sku__iexact=requested_sku)
                snapshot_queryset = SiigoProductSnapshot.objects.filter(active=True)
                if requested_skus:
                    snapshot_queryset = snapshot_queryset.filter(sku_filter)
                else:
                    snapshot_queryset = snapshot_queryset.none()
                snapshots = {
                    item.sku.upper(): item
                    for item in snapshot_queryset
                }
                missing_skus = sorted(requested_skus - set(snapshots))
                if missing_skus:
                    return Response({
                        "detail": f"Estos SKU no están en el catálogo local activo de Siigo: {', '.join(missing_skus)}.",
                        "code": "SIIGO_SKU_NOT_FOUND",
                    }, status=400)

                source_lines = [{
                    "id": line.id,
                    "quantity": line.quantity,
                    "supplier_unit_cost": line.supplier_unit_cost,
                    "supplier_line_total": line.supplier_line_total,
                    "supplier_discount_percent": line.supplier_discount_percent,
                    "supplier_discount_value": line.supplier_discount_value,
                } for line in lines_by_id.values()]
                cost_rows = calculate_supplier_commercials(
                    source_lines,
                    profile={"margin_rate": "0", "rounding_increment": "100"},
                    document={
                        "supplier_global_discount_percent": remittance.supplier_global_discount_percent,
                        "supplier_global_discount_value": remittance.supplier_global_discount_value,
                        "supplier_other_charges": remittance.supplier_other_charges,
                        "supplier_freight_cost": remittance.supplier_freight_cost,
                    },
                )
                cost_by_id = {row["id"]: row["net_unit_cost"] for row in cost_rows}

                changed_line_ids = []
                for line_id, line in lines_by_id.items():
                    submitted = submitted_by_id[line_id]
                    sku = str(submitted["siigo_sku"]).strip().upper()
                    margin = submitted["invoice_margin_percent"]
                    snapshot = snapshots.get(sku)
                    line.siigo_sku = sku
                    line.invoice_margin_percent = margin
                    line.invoice_description = (
                        str(submitted.get("invoice_description") or "").strip().upper()
                        or (snapshot.name.strip().upper() if snapshot else line.original_description)
                    )
                    line.override_reason = str(submitted.get("override_reason") or "").strip()
                    line.invoice_unit_price = (
                        calculate_siigo_invoice_price(
                            cost_by_id[line_id],
                            margin,
                            tax_rate=snapshot.tax_rate or 0,
                            tax_included=snapshot.tax_included is True,
                        )
                        if sku and snapshot and cost_by_id[line_id] is not None else None
                    )
                    line.save(update_fields=[
                        "siigo_sku", "invoice_margin_percent", "invoice_description",
                        "invoice_unit_price", "override_reason",
                    ])
                    changed_line_ids.append(line.id)

                remittance.default_margin_percent = data["default_margin_percent"]
                all_ready = all(line.siigo_sku and line.invoice_unit_price is not None for line in lines_by_id.values())
                remittance.invoice_status = (
                    Remittance.InvoiceStatus.READY
                    if all_ready and remittance.document_status == Remittance.DocumentStatus.CONFIRMED
                    else Remittance.InvoiceStatus.PENDING_CODING
                )
                remittance.version += 1
                remittance.save(update_fields=[
                    "default_margin_percent", "invoice_status", "version", "updated_at",
                ])
                RemittanceAuditEvent.objects.create(
                    remittance=remittance,
                    event_type="ACCOUNTING_COMMERCIAL_PREPARATION_UPDATED",
                    actor=request.user,
                    details={
                        "default_margin_percent": str(remittance.default_margin_percent),
                        "line_ids": changed_line_ids,
                        "siigo_price_basis": [{
                            "line_id": line.id,
                            "sku": line.siigo_sku,
                            "tax_rate": str(snapshots[line.siigo_sku].tax_rate or 0),
                            "tax_included": snapshots[line.siigo_sku].tax_included is True,
                            "invoice_unit_price": str(line.invoice_unit_price),
                        } for line in lines_by_id.values() if line.siigo_sku in snapshots],
                        "all_ready": all_ready,
                        "external_writes": 0,
                    },
                )
        except RemittanceDomainError as error:
            return Response({"detail": str(error), "code": error.code}, status=400)

        refreshed = remittance_queryset().get(pk=remittance.pk)
        return Response(serialized(refreshed, request, accounting=True))


class RemittanceInvoicePreviewAPI(RoleRequiredMixin, APIView):
    allowed_roles = ACCOUNTING_ROLES

    def get(self, request, remittance_id):
        remittance = get_object_or_404(remittance_queryset(), pk=remittance_id)
        return Response(build_invoice_preview(remittance))


class RemittanceInvoiceSiigoPreflightAPI(RoleRequiredMixin, APIView):
    """Lectura fiscal explícita: consulta Siigo, pero no crea una factura."""

    allowed_roles = ACCOUNTING_ROLES

    def post(self, request, remittance_id):
        remittance = get_object_or_404(remittance_queryset(), pk=remittance_id)
        if not settings.SIIGO_LIVE_READS_ENABLED:
            return Response({
                "detail": "La validación en vivo de Siigo está desactivada en este proceso local.",
                "code": "SIIGO_LIVE_READS_DISABLED",
                "external_writes": 0,
            }, status=503)
        try:
            result = build_live_preflight(
                remittance,
                client=SiigoReadClient(SiigoCredentials(
                    username=settings.SIIGO_USERNAME,
                    access_key=settings.SIIGO_ACCESS_KEY,
                    partner_id=settings.SIIGO_PARTNER_ID,
                )),
                document_id=settings.SIIGO_INVOICE_DOCUMENT_ID,
                payment_id=settings.SIIGO_PAYMENT_TYPE_ID,
                default_seller_id=settings.SIIGO_DEFAULT_SELLER_ID,
            )
        except SiigoPreflightError as error:
            return Response({
                "detail": error.message,
                "code": error.code,
                "details": error.details,
                "external_writes": 0,
            }, status=422)

        with transaction.atomic():
            sanitized_result = json.loads(json.dumps(result, cls=DjangoJSONEncoder))
            remittance.customer.siigo_id = result["customer"]["id"]
            remittance.customer.is_validated = True
            remittance.customer.save(update_fields=["siigo_id", "is_validated", "cached_at"])
            RemittanceInvoiceAttempt.objects.update_or_create(
                idempotency_key=result["idempotency_key"],
                defaults={
                    "remittance": remittance,
                    "status": RemittanceInvoiceAttempt.Status.PREVIEWED,
                    "sanitized_result": sanitized_result,
                    "created_by": request.user,
                },
            )
            RemittanceAuditEvent.objects.create(
                remittance=remittance,
                event_type="SIIGO_INVOICE_PREFLIGHT_VALIDATED",
                actor=request.user,
                details={
                    "customer_id": result["customer"]["id"],
                    "document_id": result["document"]["id"],
                    "payment_id": result["payment"]["id"],
                    "idempotency_key": result["idempotency_key"],
                    "external_writes": 0,
                },
            )
        return Response(result)


class RemittanceInvoiceConfirmAPI(RoleRequiredMixin, APIView):
    allowed_roles = ACCOUNTING_ROLES

    def post(self, request, remittance_id):
        if not (settings.EXTERNAL_WRITES_ENABLED and settings.SIIGO_INVOICE_WRITES_ENABLED):
            return Response({
                "detail": "La emisión real a Siigo está desactivada en este ambiente.",
                "code": "EXTERNAL_WRITES_DISABLED",
            }, status=503)
        if request.data.get("mode") != "DRAFT":
            return Response({
                "detail": "Esta fase solo permite crear un borrador en Siigo sin DIAN ni correo.",
                "code": "SIIGO_DRAFT_ONLY",
            }, status=422)
        remittance = get_object_or_404(remittance_queryset(), pk=remittance_id)
        try:
            result = issue_controlled_siigo_draft(
                remittance,
                actor=request.user,
                client=SiigoInvoiceWriteClient(SiigoCredentials(
                    username=settings.SIIGO_USERNAME,
                    access_key=settings.SIIGO_ACCESS_KEY,
                    partner_id=settings.SIIGO_PARTNER_ID,
                )),
            )
        except SiigoPreflightError as error:
            return Response({"detail": error.message, "code": error.code}, status=422)
        except SiigoIssuanceError as error:
            return Response({
                "detail": error.message,
                "code": error.code,
                "details": error.details,
            }, status=error.status_code)
        return Response(result, status=200)
