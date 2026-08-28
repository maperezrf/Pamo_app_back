# Plataforma WhatsApp local

## Flujo controlado

1. Pedidos solicita contactos válidos por despacho.
2. El operador elige de forma explícita el contacto de cada bodega.
3. La API crea o reutiliza un borrador idempotente y devuelve su vista previa.
4. El operador aprueba el borrador.
5. La API crea una sola entrada de outbox.
6. En este laboratorio, el proveedor `mock` simula la carga de la guía y el
   envío sin red. Meta permanece bloqueado.
7. Un webhook firmado puede avanzar el estado a enviado, entregado, leído o
   fallido sin almacenar el payload completo.

WhatsApp Web manual continúa disponible y no comparte la outbox.

## API local

- `GET /api/communications/whatsapp/capabilities/`
- `POST /api/communications/whatsapp/recipients/`
- `POST /api/communications/whatsapp/drafts/`
- `POST /api/communications/whatsapp/drafts/<id>/approve/`
- `POST /api/communications/whatsapp/drafts/<id>/enqueue/`
- `GET /api/communications/whatsapp/outbox/`
- `POST /api/communications/whatsapp/outbox/<id>/dispatch/`
- `GET|POST /api/communications/whatsapp/webhook/meta/`

Los endpoints de operación exigen sesión y rol. El webhook no usa sesión, pero
exige verificación/firma y coincidencia exacta de los IDs configurados.

## Seguridad

- `EXTERNAL_WRITES_ENABLED=False`
- `MESSAGING_EXTERNAL_WRITES_ENABLED=False`
- `PAMO_WHATSAPP_EXTERNAL_WRITES_ENABLED=False`
- `PAMO_WHATSAPP_PROVIDER=mock`
- Base local separada: `db_whatsapp_platform_local.sqlite3`
- Archivos locales separados: `private_uploads_whatsapp_platform_local/`
- API `127.0.0.1:8020`; frontend `127.0.0.1:5180`

No se incluyen ni imprimen tokens, IDs candidatos, teléfonos completos en
respuestas de selección, cuerpos de webhook ni respuestas del proveedor.

## Reversión

- Backend: `checkpoint/pre-whatsapp-platform-local-20260827-back`
- La rama no tiene upstream y no se autoriza push, PR, Beta o Producción.

