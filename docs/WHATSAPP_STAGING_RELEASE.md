# WhatsApp de Pedidos: entrega controlada a staging

Estado: implementación local validada; despliegue real detenido hasta completar
las credenciales y la persistencia aislada de `staging-whatsapp`.

## Destino autorizado

- Proyecto Railway: `diligent-mercy` (`78bed583-e139-48b8-a6f6-e9f7b0c7aaff`).
- Entorno: `staging-whatsapp` (`81d521fd-4da1-4992-82c9-51a5c38da049`).
- Backend: `Pamo_app_back_whatsapp_staging`
  (`02e0cbe3-1469-4c90-8364-a52c359e0f9a`).
- Frontend: `pamo_app_front_whatsapp_staging`
  (`f17482f1-811c-403e-81c2-fb2a6c80acaa`).
- Producción, Beta y los contactos reales de proveedores quedan fuera de alcance.

## Contrato seguro

- El único destinatario del piloto se configura fuera del código y debe terminar
  en `4936`; la interfaz sólo muestra los últimos cuatro dígitos.
- Las copias internas y los contactos de proveedores son registros separados.
- La notificación automática de pedidos nuevos exige bandera propia, corte
  temporal explícito y ambiente `local` o `staging-whatsapp`.
- La automatización a proveedores y el envío de guías tienen banderas separadas
  y nacen apagadas.
- El fallo de Meta queda en outbox y nunca revierte la importación del pedido.
- Un `401` bloquea la conexión y evita ciclos de reintento hasta validar un token
  nuevo.

## Flujo de novedad

El mensaje inicial usa una lista interactiva porque Meta permite un máximo de
tres botones de respuesta rápida y el flujo necesita cuatro opciones. Cada
acción firmada incluye despacho, contacto e instante de emisión y se valida
contra el `message_id` citado.

`Reportar novedad` abre una lista de seis categorías. Agotado, cantidad
incompleta y producto averiado exigen elegir el SKU y confirmar la cantidad.
Problema con la guía, retraso y otra novedad solicitan detalle. Cada paso crea un
evento aditivo y deduplicado. La confirmación final incluye pedido y resumen, sin
datos del cliente. El envío de PDFs permanece apagado.

## Puertas previas al despliegue

1. Base de datos aislada y persistente para el entorno.
2. Dominio público HTTPS del backend.
3. Secreto de firma, WABA, `phone_number_id`, token de sistema, token de
   verificación y versión de Graph API almacenados como variables selladas.
4. Webhook suscrito a `messages` y verificación GET/POST satisfactoria.
5. Allowlist con un único número y tres puertas externas habilitadas únicamente
   durante la prueba autorizada.
6. Prueba extremo a extremo y comprobación de replay sin duplicados.

El 28 de agosto de 2026 la inspección de solo lectura encontró ambos servicios
sin despliegues, dominios, base de datos ni credenciales Meta. No se desplegó ni
se modificó Railway.
