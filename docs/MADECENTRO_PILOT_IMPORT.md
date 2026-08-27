# Piloto comercial local Madecentro

## Alcance

El archivo `Madecentro_Piloto_Comercial_Utilizable.xlsx` es una propuesta comercial
local. No demuestra que los productos estén publicados en Madecentro, no contiene
inventario del canal y no autoriza cambios externos.

- Fuente preservada: `/Users/mauricioperez/Documents/PAMO_APP/_local_sources/madecentro/2026-08-26/`.
- SHA-256: `ab37b80ba57773645fd0eee32eeb9a974af98fff1323feefed23db120764ee51`.
- Escrituras externas: `0`.
- Shopify, Siigo, Madecentro y Railway: sin modificaciones.

## Reglas demostradas por el libro

- Precio Madecentro = precio público Shopify × `(1 - 20%)`.
- Precio anterior de referencia = precio público / `(1 - 20%)`, redondeado hacia arriba a $100.
- Bogotá/Cundinamarca: $11.900; gratis desde $100.000.
- Resto de Colombia: $17.900; gratis desde $150.000.
- Otros destinos: $34.900, sin umbral gratuito.

Estas tarifas son reglas del piloto basadas en Shopify. No son cotizaciones individuales
de Envía ni sustituyen el cálculo posterior por producto, bodega y pedido.

## Resultado local del 26 de agosto de 2026

- 67 SKU únicos.
- 63 SKU con precio piloto y cuatro bloqueados en el archivo.
- 63 coincidencias exactas contra el snapshot Shopify local.
- 62 precios piloto con coincidencia exacta.
- Un precio piloto ambiguo: `16165340`, presente en dos variantes locales.
- Tres SKU ausentes: `31200202`, `30600001` y `JP-140`.
- `ZW-S-6` ahora coincide exactamente, pero conserva precio pendiente porque el archivo lo bloqueó.

No se usa coincidencia aproximada por título. Los SKU ambiguos o ausentes permanecen
en revisión y no crean un producto maestro.

## Limitación comercial

El descuento de 20% define el precio del piloto, no el margen PAMO real. El objetivo de
35% antes de logística y mínimo 32% después de logística continúa pendiente de validar
contra costo Siigo, bodega de origen y logística real. Por tanto, los precios se muestran
como `PILOT_MARGIN_PENDING`, nunca como aprobados o publicados.

## Reversión

Antes de la carga se creó
`/Users/mauricioperez/Documents/PAMO_APP/Pamo_app_back/.backups/db.sqlite3.pre-madecentro-import-20260826`.
Para una reversión total, detener el backend, respaldar la base vigente y restaurar esa copia.
