# Sincronización Shopify Beta

## Alcance

Este flujo prepara y audita cambios de precio e inventario desde el catálogo
local hacia Shopify. La detección y la vista previa no escriben sistemas
externos. La ejecución está reservada a un piloto Beta con SKU exactos.

## Fuente de verdad

- Precio: costo canónico aprobado; como respaldo temporal, `inventoryItem.unitCost`
  leído de Shopify y marcado como pendiente de validar en su base tributaria.
- Inventario: exclusivamente una fuente externa canónica, vigente y con cantidad
  disponible conocida. Una lectura de Shopify nunca se reutiliza como inventario
  nuevo del proveedor.
- Ubicación: el proveedor o la bodega deben corresponder a una sola ubicación de
  Shopify. Las coincidencias ausentes o ambiguas quedan bloqueadas.

## Ciclo recurrente

El worker Beta ejecuta:

```bash
python manage.py run_shopify_sync_cycle
```

La programación se configura fuera del proceso web. `SHOPIFY_SYNC_SCAN_ENABLED`
habilita únicamente la detección local. La ejecución externa requiere además una
orden explícita con SKU, confirmación literal y todas las compuertas activas.

No se recomienda ejecutar una escritura por cada evento individual. El worker
debe consolidar los cambios durante el periodo de debounce para evitar carreras,
duplicados y consumo innecesario de la API.

## Compuertas del piloto

Todas deben cumplirse al mismo tiempo:

1. Entorno de la política: `BETA`.
2. Escrituras externas globales habilitadas.
3. Escrituras Shopify Sync habilitadas en el entorno.
4. Escrituras habilitadas en la política local.
5. Lista exacta de 1 a 5 SKU, idéntica a la lista aprobada.
6. Confirmación literal `SHOPIFY_BETA_SYNC`.
7. Huella de la propuesta sin cambios desde la vista previa.

La interfaz no puede habilitar escrituras. Esa separación es deliberada.

## Seguridad de escritura

- Precio: `productVariantsBulkUpdate`, sin actualizaciones parciales.
- Inventario: `inventorySetQuantities` con clave idempotente y
  `compareQuantity` para detectar concurrencia.
- Verificación: relectura inmediata de precio, ubicación y cantidad exactos.
- Evidencia: valor anterior, valor propuesto, fuentes, bloqueos, intentos,
  respuesta y contador de escrituras por SKU.
- Recuperación: el valor anterior queda preservado como carga de reversión; una
  reversión externa requiere una autorización separada.

## Secuencia recomendada

1. Ejecutar el worker solo en modo vista previa durante 24 a 48 horas.
2. Corregir SKU ambiguos, costos sin trazabilidad y fuentes de inventario vencidas.
3. Probar primero precio con 3 a 5 SKU heterogéneos.
4. Probar inventario por separado cuando exista una fuente real por proveedor y
   un vínculo exacto de ubicación.
5. Comparar Shopify con la relectura y conservar el resultado de cada SKU.
6. Solo después evaluar automatización continua.

## Estado local al crear el flujo

- Migración: `catalogo.0014_shopify_sync_outbox`.
- Escrituras Shopify ejecutadas: `0`.
- Scheduler Beta desplegado: no.
- Inventario real de proveedor disponible: no; los únicos registros canónicos
  no-Shopify observados pertenecen a QA ficticio y no son elegibles para piloto.
