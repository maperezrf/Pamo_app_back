# ROLLBACK — 20260827-2258-pedidos-multibodega-guias-v001

## Punto recuperable

- Backend original: `checkpoint/pedidos-dispatch-guides-local-20260827-back` en `b4b1845a9273f8a37f9978f0ee9c63f6b8933ace`.
- Frontend original: `checkpoint/pedidos-dispatch-guides-local-20260827-front` en `12c6e423e32704600d9172f1698bf38264355d16`.
- Base backend `dev`: `580cab28c0a67e379b55fcf38e8ae5df8850701a`.
- Base frontend `dev`: `9ec356d4fc09210995320021094dc4a1c8b74839`.

## Orden de reversión

1. Cerrar los PR sin merge si la revisión no aprueba el corte.
2. Eliminar posteriormente las ramas remotas de handoff solo con autorización separada.
3. Conservar las ramas y tags originales hasta terminar la revisión.
4. No modificar `dev`, Beta ni Producción como parte de esta reversión.

## Migraciones y datos

- No hay migraciones aplicadas ni datos externos escritos en este corte.
- Si se autorizan pruebas futuras, crear primero snapshot de base y verificar reversibilidad por migración.
- No se promete rollback automático de datos después de una eventual puesta en marcha.

## Criterios de parada y verificación

- Detener ante pérdida de rutas de Remisiones o Facturación, permisos inconsistentes, duplicación de pedidos, exposición de documentos o cualquier escritura externa inesperada.
- Verificar que `dev` mantiene sus SHA originales y que los PR permanecen sin merge.
- Confirmar que las ramas originales y los checkpoints continúan accesibles.
