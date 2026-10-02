from django.contrib import admin

from .models import Invoice


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("marketplace_order", "status", "number", "total", "siigo_total", "stamped", "invoice_date")
    list_filter = ("status", "stamped")
    search_fields = ("purchase_order_number", "number", "siigo_id", "marketplace_order__marketplace_order_id")
    # Lo que respondió Siigo no se edita a mano. Una factura en `creando`
    # se revisa en Siigo; si no existe, pasarla a `error` para que la
    # siguiente corrida la reintente.
    readonly_fields = (
        "marketplace_order",
        "siigo_id",
        "number",
        "siigo_total",
        "siigo_calculated_total",
        "request_payload",
        "response_payload",
        "created_at",
        "updated_at",
    )
