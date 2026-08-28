from django.conf import settings
from django.http import HttpResponse
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import RoleRequiredMixin

from .models import WhatsAppChannelConfig, WhatsAppDraft, WhatsAppOutbox
from .internal_copies import internal_copy_checkpoint, serialized_internal_recipients
from .orders_contract import (
    DraftValidationError,
    create_order_drafts,
    recipient_options,
)
from .providers import (
    ExternalWritesDisabled,
    WhatsAppProviderError,
    meta_external_writes_ready,
    normalized_phone,
)
from .serializers import draft_payload, outbox_payload
from .services import InvalidDraftState, approve_draft, dispatch_outbox, enqueue_draft
from .webhooks import WebhookValidationError, process_webhook, verify_challenge


OPERATOR_ROLES = ["Admin", "Operaciones", "Logistica", "Lider Comercial", "Gerencia"]


def actor_name(request):
    return request.user.email or request.user.username


def channel_config_payload(config):
    if not config:
        return {
            "provider": "meta_cloud_api",
            "partnerName": "",
            "displayName": "",
            "businessId": "",
            "wabaId": "",
            "phoneNumberId": "",
            "displayPhoneNumber": "",
            "connectionState": "not_linked",
            "qualityRating": "unknown",
            "webhookState": "not_configured",
            "active": False,
            "updatedBy": None,
            "updatedAt": None,
        }
    return {
        "provider": config.provider,
        "partnerName": config.partner_name,
        "displayName": config.display_name,
        "businessId": config.business_id,
        "wabaId": config.waba_id,
        "phoneNumberId": config.phone_number_id,
        "displayPhoneNumber": config.display_phone_number,
        "connectionState": config.connection_state,
        "qualityRating": config.quality_rating,
        "webhookState": config.webhook_state,
        "active": config.active,
        "updatedBy": config.updated_by or None,
        "updatedAt": config.updated_at.isoformat(),
    }


class ChannelSettingsAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATOR_ROLES
    sensitive_fragments = ("token", "secret", "password", "pin", "otp")

    def get(self, request):
        config = WhatsAppChannelConfig.objects.filter(slug="primary").first()
        return Response(
            {
                "config": channel_config_payload(config),
                "localOnly": True,
                "secretsStored": False,
                "externalWrites": 0,
            }
        )

    def put(self, request):
        rejected = [
            key
            for key in request.data.keys()
            if any(fragment in str(key).lower() for fragment in self.sensitive_fragments)
        ]
        if rejected:
            return Response(
                {
                    "detail": "Los secretos no se guardan desde esta pantalla.",
                    "rejectedFields": rejected,
                    "externalWrites": 0,
                },
                status=400,
            )

        provider = str(request.data.get("provider", "meta_cloud_api")).strip().lower()
        if provider not in {"meta_cloud_api", "mock"}:
            return Response({"provider": ["Proveedor no soportado."]}, status=400)

        identifiers = {}
        for field, key in (
            ("business_id", "businessId"),
            ("waba_id", "wabaId"),
            ("phone_number_id", "phoneNumberId"),
        ):
            value = str(request.data.get(key, "")).strip()
            if value and (not value.isdigit() or len(value) > 120):
                return Response({key: ["Debe contener únicamente dígitos."]}, status=400)
            identifiers[field] = value

        connection_state = str(request.data.get("connectionState", "not_linked"))
        quality_rating = str(request.data.get("qualityRating", "unknown"))
        webhook_state = str(request.data.get("webhookState", "not_configured"))
        if connection_state not in dict(WhatsAppChannelConfig.CONNECTION_STATES):
            return Response({"connectionState": ["Estado inválido."]}, status=400)
        if quality_rating not in dict(WhatsAppChannelConfig.QUALITY_RATINGS):
            return Response({"qualityRating": ["Calidad inválida."]}, status=400)
        if webhook_state not in dict(WhatsAppChannelConfig.WEBHOOK_STATES):
            return Response({"webhookState": ["Estado de webhook inválido."]}, status=400)

        display_phone_number = str(request.data.get("displayPhoneNumber", "")).strip()
        phone_digits = "".join(character for character in display_phone_number if character.isdigit())
        if display_phone_number and len(phone_digits) < 10:
            return Response({"displayPhoneNumber": ["Número incompleto."]}, status=400)

        config, _ = WhatsAppChannelConfig.objects.update_or_create(
            slug="primary",
            defaults={
                "provider": provider,
                "partner_name": str(request.data.get("partnerName", "")).strip()[:120],
                "display_name": str(request.data.get("displayName", "")).strip()[:160],
                "display_phone_number": display_phone_number[:40],
                "connection_state": connection_state,
                "quality_rating": quality_rating,
                "webhook_state": webhook_state,
                "active": bool(request.data.get("active", False)),
                "updated_by": actor_name(request),
                **identifiers,
            },
        )
        return Response(
            {
                "config": channel_config_payload(config),
                "localOnly": True,
                "secretsStored": False,
                "detail": "Configuración local guardada. Meta no fue modificado.",
                "externalWrites": 0,
            }
        )


class CapabilitiesAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATOR_ROLES

    def get(self, request):
        readiness = meta_external_writes_ready()
        provider = str(settings.PAMO_WHATSAPP_PROVIDER or "mock").lower()
        pilot = normalized_phone(settings.PAMO_WHATSAPP_PILOT_RECIPIENT)
        return Response(
            {
                "provider": provider,
                "mockMode": provider == "mock",
                "humanApprovalRequired": True,
                "automaticPilot": bool(
                    settings.PAMO_WHATSAPP_AUTO_PREPARE_ENABLED
                    and settings.PAMO_WHATSAPP_SUPPLIER_AUTOMATION_ENABLED
                    and pilot.endswith("4936")
                ),
                "internalOrderNotificationsEnabled": bool(
                    settings.PAMO_WHATSAPP_INTERNAL_ORDER_NOTIFICATIONS_ENABLED
                ),
                "deploymentTier": settings.PAMO_WHATSAPP_DEPLOYMENT_TIER,
                "pilotRecipientMasked": f"••••{pilot[-4:]}" if pilot else None,
                "internalRecipients": serialized_internal_recipients(),
                "internalCopyCheckpoint": (
                    internal_copy_checkpoint().isoformat()
                    if internal_copy_checkpoint()
                    else None
                ),
                "manualWhatsAppWebFallback": True,
                "externalWritesEnabled": provider == "meta" and readiness["ready"],
                "gates": {
                    "global": bool(settings.EXTERNAL_WRITES_ENABLED),
                    "messaging": bool(settings.MESSAGING_EXTERNAL_WRITES_ENABLED),
                    "whatsapp": bool(settings.PAMO_WHATSAPP_EXTERNAL_WRITES_ENABLED),
                },
                "meta": {
                    "configured": not readiness["missingConfiguration"],
                    "missingConfiguration": readiness["missingConfiguration"],
                    "allowlistConfigured": readiness["allowlistConfigured"],
                },
                "externalWrites": 0,
            }
        )


class RecipientOptionsAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATOR_ROLES

    def post(self, request):
        shipment_ids = request.data.get("shipment_ids", [])
        if not isinstance(shipment_ids, list) or not shipment_ids or len(shipment_ids) > 100:
            return Response(
                {"shipment_ids": ["Selecciona entre 1 y 100 despachos."]}, status=400
            )
        try:
            options = recipient_options(shipment_ids)
        except DraftValidationError as error:
            return Response(error.errors, status=400)
        return Response({"shipments": options, "externalWrites": 0})


class DraftPreviewAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATOR_ROLES

    def post(self, request):
        try:
            drafts, created = create_order_drafts(
                selections=request.data.get("selections", []),
                actor=actor_name(request),
            )
        except DraftValidationError as error:
            return Response(error.errors, status=400)
        return Response(
            {
                "drafts": [draft_payload(item) for item in drafts],
                "created": created,
                "reused": len(drafts) - created,
                "detail": "Borradores listos para revisión humana. Nada se envió.",
                "externalWrites": 0,
            },
            status=201 if created else 200,
        )


class DraftActionAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATOR_ROLES

    def post(self, request, draft_id, action):
        try:
            if action == "approve":
                draft = approve_draft(draft_id=draft_id, actor=actor_name(request))
                if not draft:
                    return Response({"detail": "Borrador no encontrado."}, status=404)
                return Response({"draft": draft_payload(draft), "externalWrites": 0})
            if action == "enqueue":
                outbox, created = enqueue_draft(draft_id=draft_id)
                if not outbox:
                    return Response({"detail": "Borrador no encontrado."}, status=404)
                return Response(
                    {
                        "outbox": outbox_payload(outbox),
                        "created": created,
                        "externalWrites": 0,
                    },
                    status=201 if created else 200,
                )
        except InvalidDraftState as error:
            return Response({"detail": str(error), "externalWrites": 0}, status=409)
        return Response({"detail": "Acción inválida."}, status=400)


class OutboxAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATOR_ROLES

    def get(self, request):
        items = WhatsAppOutbox.objects.select_related("draft")[:200]
        return Response(
            {
                "outbox": [
                    {**outbox_payload(item), "draft": draft_payload(item.draft)}
                    for item in items
                ],
                "externalWrites": 0,
            }
        )


class OutboxDispatchAPI(RoleRequiredMixin, APIView):
    allowed_roles = OPERATOR_ROLES

    def post(self, request, outbox_id):
        try:
            outbox, dispatched = dispatch_outbox(outbox_id=outbox_id)
        except ExternalWritesDisabled as error:
            return Response(
                {"detail": str(error), "code": error.code, "externalWrites": 0},
                status=409,
            )
        except WhatsAppProviderError as error:
            return Response(
                {"detail": str(error), "code": error.code, "externalWrites": 0},
                status=422,
            )
        if not outbox:
            return Response({"detail": "Elemento de outbox no encontrado."}, status=404)
        return Response(
            {
                "outbox": outbox_payload(outbox),
                "dispatched": dispatched,
                "simulation": outbox.provider == "mock",
                "externalWrites": 0 if outbox.provider == "mock" else int(dispatched),
            }
        )


class MetaWebhookAPI(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        try:
            challenge = verify_challenge(
                mode=request.query_params.get("hub.mode"),
                token=request.query_params.get("hub.verify_token"),
                challenge=request.query_params.get("hub.challenge"),
            )
        except WebhookValidationError as error:
            return Response({"detail": str(error), "code": error.code}, status=403)
        return HttpResponse(challenge, content_type="text/plain")

    def post(self, request):
        try:
            result = process_webhook(
                raw_body=request.body,
                signature_header=request.headers.get("X-Hub-Signature-256", ""),
            )
        except WebhookValidationError as error:
            return Response({"detail": str(error), "code": error.code}, status=403)
        return Response({**result, "externalWrites": 0})
