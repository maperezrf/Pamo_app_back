# No define nada -- solo reexporta los modelos de cada proveedor para que
# Django los reconozca como parte de la app "integrations" (ver
# lineamientos-backend.md §3.2.2). Un proveedor sin modelos propios (hoy:
# shopify, falabella, sodimac, envia, whatsapp) simplemente no aparece acá.

from .mercadolibre.models import MercadoLibreToken  # noqa: F401
from .siigo.models import SiigoToken  # noqa: F401
