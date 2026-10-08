from integrations.shopify.functions.list_locations import list_locations

from ..models import DispatchLocation


def sync_dispatch_locations(params=None, progress_callback=None, cancellation_token=None):
    """Proceso `orders.sync_dispatch_locations`: trae las ubicaciones de
    Shopify al registro de bodegas de despacho.

    Crea las nuevas y actualiza nombre, ciudad y si está activa. **Nunca**
    toca los canales ni los contactos: esos se configuran a mano. Una
    bodega que desaparece de Shopify no se borra (puede tener despachos);
    queda como inactiva.

    Solo lee Shopify. Devuelve {"created", "updated", "deactivated"}.
    """
    progress_callback = progress_callback or (lambda percent, step=None: None)
    progress_callback(0, "Leyendo bodegas de Shopify")
    locations = list_locations()

    created = updated = 0
    for location in locations:
        _, was_created = DispatchLocation.objects.update_or_create(
            shopify_location_id=location["location_id"],
            defaults={
                "name": location["name"],
                "city": location["city"],
                "is_active": location["is_active"],
                "origin_address": location.get("address", ""),
                "origin_province_code": location.get("province_code", ""),
                "origin_zip": location.get("zip", ""),
                "origin_phone": location.get("phone", ""),
            },
        )
        created += was_created
        updated += not was_created

    seen = [location["location_id"] for location in locations]
    deactivated = DispatchLocation.objects.exclude(shopify_location_id__in=seen).filter(is_active=True).update(
        is_active=False
    )
    summary = {"created": created, "updated": updated, "deactivated": deactivated}
    progress_callback(100, f"{created} nuevas, {updated} actualizadas, {deactivated} desactivadas")
    return summary
