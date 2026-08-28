from django.urls import path

from .views import (
    CapabilitiesAPI,
    ChannelSettingsAPI,
    DraftActionAPI,
    DraftPreviewAPI,
    MetaWebhookAPI,
    OutboxAPI,
    OutboxDispatchAPI,
    RecipientOptionsAPI,
)


urlpatterns = [
    path("whatsapp/settings/", ChannelSettingsAPI.as_view(), name="whatsapp-settings"),
    path("whatsapp/capabilities/", CapabilitiesAPI.as_view(), name="whatsapp-capabilities"),
    path("whatsapp/recipients/", RecipientOptionsAPI.as_view(), name="whatsapp-recipients"),
    path("whatsapp/drafts/", DraftPreviewAPI.as_view(), name="whatsapp-drafts"),
    path(
        "whatsapp/drafts/<uuid:draft_id>/<str:action>/",
        DraftActionAPI.as_view(),
        name="whatsapp-draft-action",
    ),
    path("whatsapp/outbox/", OutboxAPI.as_view(), name="whatsapp-outbox"),
    path(
        "whatsapp/outbox/<uuid:outbox_id>/dispatch/",
        OutboxDispatchAPI.as_view(),
        name="whatsapp-outbox-dispatch",
    ),
    path("whatsapp/webhook/meta/", MetaWebhookAPI.as_view(), name="whatsapp-meta-webhook"),
]
