# HANDOFF — 20260827-2258-pedidos-multibodega-guias-v001

## Resumen ejecutivo

Este corte prepara para revisión el flujo local de Pedidos y logística de PAMO APP: importación canónica, pedidos con varios despachos y bodegas, recuperación y clasificación de guías PDF, asignación manual auditable, filtros operativos, cotización previa sin compra automática y mensajería de proveedores protegida por banderas. Conserva Remisiones y Facturación de `dev`, mantiene las escrituras externas apagadas y no incluye merge, despliegue ni cambios en Producción.

## Identificación

- Nombre: Pedidos multibodega, guías y operación logística
- Versión: 0.1.0-prototype
- Fecha: 2026-08-27
- Módulo: Pedidos / Logística
- Responsable: Mauricio Pérez
- Origen: desarrollo local aislado en PAMO APP
- Estado: READY_FOR_REVIEW
- PR backend: https://github.com/maperezrf/Pamo_app_back/pull/8
- PR frontend: https://github.com/maperezrf/pamo_app_front/pull/11
- Registro de Prototipos: `37431612-bdbb-48b5-820a-cc014ccf58e6`

## Objetivo y alcance

### Problema y necesidad

Dar a la operación una vista única y auditable de pedidos multicanal, despachos por bodega y disponibilidad de guías, reduciendo cargas manuales y sin crear un segundo consumidor ni comprar etiquetas de forma automática.

### Incluido

- Importación canónica y actualización local idempotente de pedidos.
- Separación de un pedido en uno o varios despachos por ubicación Shopify.
- Asignación manual de bodega por despacho con origen y auditoría visibles.
- Recuperación, clasificación, caché y descarga controlada de guías PDF.
- Filtros de pedidos sin guía, con guía y sin trazabilidad.
- Preflight de cotización y selección humana de transportadora.
- Estado de conectores y disponibilidad de datos, sin confundir salud con cobertura.
- Flujo de contactos y novedades por bodega protegido por banderas externas.
- Diseño integrado con la navegación de PAMO APP y patrones del Catálogo multicanal.

### No incluido

- Merge a `dev` o `main`.
- Despliegue a Beta, Railway o Producción.
- Aplicación de migraciones sobre bases reales.
- Compra o generación automática de guías.
- Reprocesamiento masivo de históricos.
- Envíos reales a proveedores o clientes.
- Automatización autónoma de selección de transportadora.

## Funcionamiento

El importador conserva el pedido comercial y crea despachos separados cuando existen varias ubicaciones. Cada despacho mantiene bodega, artículos y guía propios. El operador puede corregir la bodega de forma explícita y auditable, consultar la fuente de la asignación, descargar el PDF disponible y filtrar pendientes. La cotización solamente prepara opciones elegibles; la decisión y cualquier futura compra continúan bajo aprobación humana.

## Frontend

- Rama: `handoff/pedidos-multibodega-guides-20260827`.
- Base: `dev` en `9ec356d4fc09210995320021094dc4a1c8b74839`.
- Implementación: `84c512df913521710e104cf0eab378c7189f85ab`.
- Integra Pedidos, Catálogo y Logística en la navegación vigente, preservando Remisiones.
- Incluye filtros operativos, panel de despacho, estados de guía y disposición responsive.

## Backend

- Rama: `handoff/pedidos-multibodega-guides-20260827`.
- Base: `dev` en `580cab28c0a67e379b55fcf38e8ae5df8850701a`.
- Implementación: `5b91751e2d88851bfe29cdec75bee691021f2d5c`.
- Modela pedidos, artículos, despachos, plan de envío, guías y eventos de proveedor.
- Expone lectura, actualización local, descarga privada y preflight de cotización.
- Preserva Facturación y el contrato vigente de Remisiones.

## Repositorios relacionados y dependencias

- Backend: `maperezrf/Pamo_app_back`.
- Frontend: `maperezrf/pamo_app_front`.
- Shopify, Mercado Libre, Falabella, Sodimac y Envía siguen desacoplados mediante adaptadores y banderas.
- La rama incluye la base local del Catálogo multicanal porque Pedidos consume sus ubicaciones, medidas y cotización. Los PR históricos de Catálogo no se modifican ni reutilizan.

## Datos, migraciones y seguridad

- Incluye migraciones de Catálogo `0001`–`0015`, Pedidos `0001`–`0006` y Communications `0001`–`0003`.
- No se aplicó ninguna migración a una base compartida o real.
- No se incluyeron bases locales, tokens, archivos `.env`, logs ni artefactos compilados.
- `externalWrites=0` durante desarrollo y QA; Shopify, Envía y mensajería real permanecieron desactivados.
- Excepción documentada: el escáner `assigned-secret` marcó expresiones dinámicas seguras —lecturas de entorno, valor de prueba, token de webhook y token generado en ejecución—. Se verificó que no hay un secreto literal versionado; no se modificó el código para silenciar el control.

## Gobernanza y pruebas

- Consultados: mapa documental, lineamientos generales, frontend, backend, identidad visual, Git, Prototipos y listado vigente de prototipos.
- Backend: `manage.py check` aprobado y 256 pruebas aprobadas.
- Frontend: lint aprobado con tres advertencias heredadas de Catálogo y compilación aprobada.
- QA visual original: escritorio y móvil sin desbordamiento horizontal; el corte de integración preservó las rutas y pantallas vigentes.

## Despliegue y rollback

- Despliegue: NO REALIZADO.
- Merge: NO REALIZADO.
- Los dos PR están en estado Draft contra `dev`.
- Puntos originales: `checkpoint/pedidos-dispatch-guides-local-20260827-back` y `checkpoint/pedidos-dispatch-guides-local-20260827-front`.
- La rama de handoff puede cerrarse sin afectar las ramas originales ni `dev`.

## Riesgos y pendientes

- El cambio es amplio porque Catálogo aún no forma parte de `dev`; requiere revisión conjunta del contrato Catálogo–Pedidos.
- Las migraciones deben probarse sobre una copia de datos antes de cualquier despliegue.
- La cobertura real de PDFs depende de lo que cada canal exponga y debe medirse por fuente.
- La compra masiva de guías y los envíos reales deben conservar autorización independiente.

## Instrucciones para Miguel Ángel

1. Revisar primero los contratos de Pedidos, despachos y guía PDF.
2. Confirmar que Remisiones y Facturación continúan disponibles.
3. Ejecutar migraciones únicamente sobre una base de prueba desechable.
4. Validar un pedido de una bodega y otro multibodega.
5. Validar filtros sin guía, con guía sin trazabilidad y descarga de PDF.
6. Confirmar que las banderas de escrituras externas permanecen apagadas.
7. Aprobar o solicitar cambios en los PR; no mergear ni desplegar desde este expediente.
