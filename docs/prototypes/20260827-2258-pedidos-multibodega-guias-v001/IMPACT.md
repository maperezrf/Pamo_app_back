# IMPACT — 20260827-2258-pedidos-multibodega-guias-v001

## Funcional y operativo

- Permite operar pedidos con uno o varios despachos y bodegas.
- Reduce reproceso al clasificar disponibilidad de PDF y separar pendientes de trazabilidad.
- Mantiene selección humana para bodega, transportadora y futuras compras.
- La mensajería externa continúa protegida y no condiciona la importación del pedido.

## Técnico e integraciones

- Añade dominios separados de Catálogo, Pedidos y Communications.
- Conserva contratos de Remisiones y Facturación de `dev`.
- Shopify y canales actúan como fuentes canónicas; los conectores externos permanecen desacoplados e idempotentes.
- El alcance del PR es amplio porque Catálogo aún no se encuentra en `dev`.

## Datos, seguridad y permisos

- Se proponen nuevas tablas e índices; no se alteró una base real.
- Documentos de guía se sirven mediante rutas privadas y no por exposición pública del almacenamiento.
- Los permisos se resuelven en backend y la UI solamente refleja capacidades.
- No se versionaron secretos ni datos personales completos.

## Coste, compatibilidad y reversibilidad

- Coste externo durante el handoff: ninguno.
- La integración es reversible cerrando la rama/PR porque no hay merge, despliegue ni migración aplicada.
- Antes de desplegar se requiere prueba de migración, revisión de compatibilidad y un rollback de base específico.
- Automatizar compra de guías ahora aumentaría riesgo y coste; queda fuera hasta disponer de evidencia suficiente.
