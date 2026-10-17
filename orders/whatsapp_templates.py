# Plantillas de WhatsApp aprobadas que envía `orders` (por Zernio, ver
# integrations/zernio/). La clave es el uso dentro del backend; `name` y
# `language` deben coincidir exactamente con la plantilla aprobada en Meta
# (se ven con `python manage.py send_test_whatsapp --list`). Cambiar una
# plantilla es cambiar este diccionario y desplegar.
#
# Cada entrada documenta qué variables manda el código, en orden: si la
# plantilla aprobada pide otra cantidad, Zernio rechaza el envío
# (INVALID_TEMPLATE_PARAMS). Un `name` vacío deja el envío en error.
WHATSAPP_TEMPLATES = {
    # Aviso de despacho a la bodega (orders/functions/notify_dispatch.py),
    # aprobada en Meta como `despacho_pedido`:
    # el listado de pedidos en PDF (`dispatch_list_pdf`: pedido, imagen,
    # SKU, producto, cantidad y link a la guía). Hoy sale con un pedido
    # (botón "Notificar a proveedor" y el proceso); el aviso masivo usará
    # la misma. Encabezado: documento. Cuerpo: {{1}} bodega, {{2}} cuántos
    # pedidos.
    "despacho_bodega": {"name": "despacho_pedido", "language": "es_CO"},
}
