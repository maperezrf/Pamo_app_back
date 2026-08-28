# Plataforma WhatsApp local: contrato de aislamiento

Estado: capa local implementada y en validación. Ninguna conexión externa está activa.

## Propósito

Construir una capacidad reutilizable de mensajería para Pedidos y futuros
módulos sin acoplar sus reglas de negocio a Meta, Vambe u otro proveedor.

## Punto de partida y reversión

- Rama local: `feature/whatsapp-platform-local-20260827`.
- Base: `8b0a0603b16c2a2ca3a90ba6b5fba4bba61e6e94`.
- Reversión local: `checkpoint/pre-whatsapp-platform-local-20260827-back`.
- Sin upstream, push, PR, Beta o Producción.

## Límites obligatorios

1. No importar credenciales, variables ni bases de datos desde otros worktrees.
2. No modificar interruptores de Pedidos, Remisiones, Catálogo, Shopify,
   Envía, Mercado Libre, Falabella o Sodimac.
3. No llamar Meta, Vambe ni otro proveedor mientras las dos puertas siguientes
   no estén habilitadas expresamente:
   - puerta global de escrituras externas;
   - puerta específica `PAMO_WHATSAPP_EXTERNAL_WRITES_ENABLED`.
4. La puerta específica debe ser `false` por defecto y fallar cerrada si falta.
5. No guardar tokens, secretos, PIN, OTP ni cuerpos sensibles en Git, SQLite,
   logs, pruebas o documentación.
6. No codificar IDs de portafolio, WABA o número en el código.

## Aislamiento local previsto

Estos valores quedan reservados para este laboratorio y no se comparten con
otros módulos:

- API: `127.0.0.1:8020`.
- Base: `db_whatsapp_platform_local.sqlite3`.
- Archivos: `private_uploads_whatsapp_platform_local/`.
- Frontend consumidor: `127.0.0.1:5180`.
- Variables propias con prefijo `PAMO_WHATSAPP_` o `META_`.

No se deben reutilizar los puertos `8010`, `8012`, `5173`, `5175` o los usados
por otros laboratorios activos.

## Arquitectura requerida

- Aplicación Django compartida `communications` o nombre equivalente.
- Contrato de proveedor independiente de Meta.
- Adaptador `MetaWhatsAppClient` únicamente en backend.
- Outbox idempotente, historial de intentos y estados de entrega.
- Webhook firmado, deduplicado y validado contra WABA y teléfono esperados.
- Pedidos publica una solicitud de mensajería; no ejecuta Graph API.
- WhatsApp Web manual permanece como reversión operativa.
- Toda integración real comienza con mock y destinatarios de prueba.

## Estado implementado

- Contrato desacoplado de proveedor y `MetaWhatsAppClient` sólo en backend.
- Proveedor `mock` predeterminado, sin red ni escrituras externas.
- Borradores inmutables por clave idempotente y aprobación humana explícita.
- Outbox con estados `pending`, `sent`, `delivered`, `read` y `failed`.
- Historial de intentos sin token, cuerpo de respuesta ni datos completos en logs.
- Webhook GET/POST con HMAC, deduplicación y validación de WABA/teléfono.
- Guía PDF leída del almacenamiento privado y convertida a `media_id` simulado.
- Contacto validado contra la bodega de cada despacho antes de crear el borrador.
- WhatsApp Web manual conservado como fallback independiente.

La simulación local puede cambiar estados en la base aislada, pero siempre
reporta `externalWrites=0`. El adaptador Meta no puede operar mientras alguna
de las tres compuertas permanezca apagada.

## Promoción

La rama no se incorpora a otra rama hasta aprobar `shared + orders +
integrations + release-safety`, demostrar que no cambia interruptores ajenos y
recibir autorización separada para push, Beta, secretos y envíos reales.
