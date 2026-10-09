from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path

from .functions.send_test_notification import (
    TestNotificationError, email_settings_summary, send_test_email, send_test_whatsapp,
)
from .functions.sync_dispatch_locations import sync_dispatch_locations
from .models import Dispatch, DispatchLocation, DispatchNotification, MarketplaceOrder, MarketplaceOrderItem


class MarketplaceOrderItemInline(admin.TabularInline):
    model = MarketplaceOrderItem
    extra = 0
    # `inventory_snapshot`: stock por bodega que se evaluó al elegir la
    # bodega del pedido -- lo que necesita ver quien resuelve una novedad.
    readonly_fields = ("marketplace_sku", "quantity", "unit_price", "shopify_variant_id", "inventory_snapshot")


@admin.register(MarketplaceOrder)
class MarketplaceOrderAdmin(admin.ModelAdmin):
    list_display = (
        "marketplace",
        "marketplace_order_number",
        "customer_identification",
        "status",
        "marketplace_status",
        "shopify_order_name",
        "fulfillment_status",
        "fulfillment_location_name",
        "updated_at",
    )
    list_filter = ("marketplace", "status", "marketplace_status", "fulfillment_status")
    search_fields = ("marketplace_order_id", "marketplace_order_number", "customer_identification", "shopify_order_id")
    inlines = [MarketplaceOrderItemInline]
    # Una novedad se resuelve a mano: elegir fulfillment_location_id/_name,
    # pasar fulfillment_status a "resuelta_manual" y dejar la nota. El
    # proceso no vuelve a tocar un pedido resuelto manualmente.


@admin.register(DispatchLocation)
class DispatchLocationAdmin(admin.ModelAdmin):
    """Registro de bodegas de despacho. Las filas salen de Shopify (acción
    "Sincronizar"); aquí solo se configuran canales y contactos."""

    list_display = (
        "name", "city", "is_active", "notify_api", "notify_email", "notify_whatsapp", "requires_label", "creates_own_label",
    )
    list_filter = ("is_active", "notify_api", "notify_email", "notify_whatsapp")
    readonly_fields = ("shopify_location_id", "name", "city", "is_active", "updated_at")
    fields = (
        ("name", "city", "is_active"),
        "shopify_location_id",
        ("notify_api", "notify_email", "notify_whatsapp"),
        "emails",
        "whatsapp_numbers",
        "envia_warehouse_id",
        ("requires_label", "creates_own_label"),
        "updated_at",
    )
    actions = ["sync_from_shopify"]
    # Botón "Probar avisos": correo y WhatsApp de prueba enviados desde el
    # servidor (sirve en Railway sin CLI).
    change_list_template = "admin/orders/dispatchlocation/change_list.html"

    def get_urls(self):
        test_view = self.admin_site.admin_view(self.test_notifications_view)
        return [path("probar-avisos/", test_view, name="orders_dispatchlocation_test_notifications"), *super().get_urls()]

    def test_notifications_view(self, request):
        """Envía un correo o un WhatsApp de prueba a quien prueba. Solo
        superusuarios: manda mensajes reales con las credenciales del
        servidor."""
        if not request.user.is_superuser:
            raise PermissionDenied
        if request.method == "POST":
            to = request.POST.get("to", "").strip()
            try:
                if request.POST.get("channel") == "whatsapp":
                    message_id = send_test_whatsapp(to)
                    self.message_user(request, f"WhatsApp enviado a {to} (id {message_id}).", messages.SUCCESS)
                else:
                    if not to:
                        raise TestNotificationError("Escribe el correo de destino.")
                    elapsed = send_test_email(to)
                    self.message_user(request, f"Correo enviado a {to} en {elapsed:.1f}s.", messages.SUCCESS)
            except TestNotificationError as error:
                self.message_user(request, str(error), messages.ERROR)
            return redirect(request.path)
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Probar avisos",
            "email_settings": email_settings_summary(),
        }
        return TemplateResponse(request, "admin/orders/dispatchlocation/test_notifications.html", context)

    def has_add_permission(self, request):
        return False  # se crean al sincronizar con Shopify

    def has_delete_permission(self, request, obj=None):
        return False  # pueden tener despachos; una bodega quitada queda inactiva

    @admin.action(description="Sincronizar bodegas desde Shopify (solo lectura en Shopify)")
    def sync_from_shopify(self, request, queryset):
        summary = sync_dispatch_locations()
        self.message_user(
            request,
            f"Bodegas: {summary['created']} nuevas, {summary['updated']} actualizadas, {summary['deactivated']} desactivadas.",
            messages.SUCCESS,
        )


class DispatchNotificationInline(admin.TabularInline):
    model = DispatchNotification
    extra = 0
    can_delete = False
    readonly_fields = ("channel", "status", "recipient", "external_id", "error_description", "sent_at", "updated_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Dispatch)
class DispatchAdmin(admin.ModelAdmin):
    list_display = ("shopify_order", "channel", "location", "status", "tracking_number", "updated_at")
    list_filter = ("status", "channel", "location")
    search_fields = ("shopify_order__name", "shopify_order__shopify_id", "tracking_number")
    readonly_fields = (
        "shopify_order",
        "channel",
        "tracking_number",
        "label_sha256",
        "label_fetched_at",
        "created_at",
        "updated_at",
    )
    inlines = [DispatchNotificationInline]
    # Se puede corregir a mano la bodega, el estado o la nota (p. ej. pasar
    # un `manual` resuelto a `esperando_guia` para que el proceso siga).
