from django.db import models


class ShopifyCustomer(models.Model):
    """Directorio local sincronizado con los clientes de Shopify -- ver
    docs/implementations-plans/shopify-customers-directory.md para el porqué:
    la API de Shopify no permite buscar un cliente por `company` (donde
    vive la cédula), así que la resolución "¿ya existe este cliente?" se
    hace contra esta tabla, no contra Shopify en vivo.

    Se mantiene al día por dos mecanismos independientes: el webhook
    (tiempo real) y la reconciliación periódica (red de seguridad +
    backfill). Ambos hacen upsert vía la misma función
    (`customers.functions.upsert_from_shopify_customer`).
    """

    shopify_id = models.CharField(max_length=32, unique=True)
    identification = models.CharField(max_length=32, blank=True, db_index=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    email = models.CharField(max_length=254, blank=True, db_index=True)
    phone = models.CharField(max_length=32, blank=True, db_index=True)
    # Se guarda tal cual lo devuelve Shopify, sin recortar ningún prefijo:
    # el formato exacto del id de una MailingAddress no está confirmado (a
    # diferencia del id de Customer, que sí se usa recortado en
    # integrations/shopify/functions/create_order.py) -- longitud generosa
    # a propósito por si no es un simple `gid://shopify/MailingAddress/<n>`.
    default_address_id = models.CharField(max_length=255, blank=True)
    shopify_updated_at = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Cliente de Shopify"
        verbose_name_plural = "Clientes de Shopify"

    def __str__(self):
        return f"{self.shopify_id} ({self.identification or 'sin cédula'})"
