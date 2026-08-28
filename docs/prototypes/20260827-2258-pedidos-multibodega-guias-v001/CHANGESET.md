# CHANGESET — 20260827-2258-pedidos-multibodega-guias-v001

## Front

- Base SHA: `9ec356d4fc09210995320021094dc4a1c8b74839`
- Implementación SHA: `84c512df913521710e104cf0eab378c7189f85ab`
- Cambios: 35 archivos frente a `dev`; 12.099 inserciones y 29 eliminaciones. Añade áreas de Catálogo, Pedidos, Logística y Communications, e integra rutas y menú preservando Remisiones.

## Back

- Base SHA: `580cab28c0a67e379b55fcf38e8ae5df8850701a`
- Implementación SHA: `5b91751e2d88851bfe29cdec75bee691021f2d5c`
- Cambios: 178 archivos frente a `dev`; 29.223 inserciones y 11 eliminaciones. Añade las áreas `catalogo`, `pedidos` y `communications`, integraciones y documentación; conserva Facturación y Remisiones.

## Repositorios relacionados

- No se modificaron repositorios Shopify, Railway ni servicios externos.
- No se modificaron los PR históricos de Catálogo.

## Migraciones, dependencias, variables y configuración

- Catálogo: migraciones `0001` a `0015`.
- Pedidos: migraciones `0001` a `0006`.
- Communications: migraciones `0001` a `0003`.
- Dependencia backend añadida: `pypdf` para inspección y manejo controlado de documentos.
- Variables nuevas se documentan solamente por nombre en `.env.example`; no hay valores sensibles.
- Configuración: registro de apps, URLs, caché, almacenamiento privado de guías/documentos y menú por permisos.
- Endpoints: pedidos canónicos, sincronización local, despachos, documentos privados, cotización/preflight, conectores y mensajería protegida.
- Tests: perfiles de Catálogo, Pedidos, Communications, scheduler, importador, etiquetas y plan de envío.
