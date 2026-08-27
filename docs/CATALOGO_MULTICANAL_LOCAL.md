# Catálogo, costos y precios multicanal — laboratorio local

Estado: `CANDIDATO LOCAL PROBADO`. La aplicación no consulta canales durante
la navegación: consume snapshots guardados en SQLite. Se ejecutaron lecturas
puntuales autorizadas de Shopify, Siigo y Falabella mediante mecanismos seguros, sin
mostrar ni persistir credenciales. `EXTERNAL_WRITES_ENABLED=False`,
`SIIGO_INVOICE_WRITES_ENABLED=False` y `SHOPIFY_READS_ENABLED=False` mantienen
desactivada cualquier escritura o sincronización continua.

## Inicio local

Los servicios pertenecen a los dos proyectos ya configurados dentro de
`/Users/mauricioperez/Documents/PAMO_APP`:

```bash
cd /Users/mauricioperez/Documents/PAMO_APP/Pamo_app_back
.venv/bin/python manage.py runserver 127.0.0.1:8010

cd /Users/mauricioperez/Documents/PAMO_APP/pamo_app_front
npm run dev -- --host 127.0.0.1 --port 5173
```

- Interfaz: `http://127.0.0.1:5173/catalogo-multicanal`
- API: `http://127.0.0.1:8010/api/catalogo/workspace/`
- Persistencia: `Pamo_app_back/db.sqlite3`
- Detener: `Ctrl+C` en el proceso de inicio.

## Persistencia

- Sin `DATABASE_URL`, Django crea `db.sqlite3` dentro del clon local.
- Es una base nueva, temporal y aislada. No representa PostgreSQL de nube.
- El modelo usa tipos compatibles con Django para migrar después a PostgreSQL.
- `python manage.py seed_catalog_demo` crea exclusivamente fixtures QA y nunca
  crea, sustituye ni elimina el proveedor Barú real.
- La base contiene 3.159 productos Shopify vigentes y 3.364 variantes Shopify,
  además de 83 productos/variantes locales de proveedor. También conserva
  5.445 productos Siigo, 908 publicaciones Falabella y 1.761 filas normalizadas
  de Mercado Libre correspondientes a 1.759 publicaciones.
- La API pagina antes de serializar (50 productos por página por defecto, máximo
  100) y ejecuta los
  filtros principales contra toda la base local.

## Contrato Shopify

1. Importación inicial paginada por cursor a snapshots locales: probada en 62
   páginas. Los campos no ofrecidos por el conector usado quedaron pendientes.
2. Actualización incremental por cursor y `updated_at` persistidos.
3. Webhooks futuros idempotentes mediante `WebhookInbox`.
4. Conciliación de catálogo proveedor contra variantes solo por SKU exacto.
5. Duplicados, faltantes y ambigüedades quedan en revisión.
6. Toda futura escritura sigue: vista previa, lote, aprobación, historial,
   ejecución explícita e inversa/rollback cuando la API del canal lo permita.

### Contrato logístico canónico

- El peso empacado canónico vive por variante en
  `InventoryItem.measurement.weight`.
- Largo, ancho y alto viven por variante en los metacampos Shopify
  `logistica.largo_paquete`, `logistica.ancho_paquete` y
  `logistica.alto_paquete`, todos de tipo `dimension`.
- `logistica.fragil` y `logistica.fecha_revision` conservan manejo especial
  y fecha de verificación.
- `logistica.peso_empacado` se admite únicamente como compatibilidad con
  cargas anteriores. Si difiere del peso nativo, el registro queda en revisión.
- La base interna es un snapshot de lectura optimizado; nunca sustituye a
  Shopify como fuente maestra de peso o dimensiones.

## Rendimiento y continuidad

La navegación nunca espera respuestas en vivo de Shopify, Siigo, Envía o un
marketplace. Los conectores alimentan snapshots normalizados mediante procesos
incrementales; la interfaz pagina y filtra en el servidor.

- La última vista correcta se presenta inmediatamente desde caché privada del
  navegador y se revalida en segundo plano.
- La API mantiene una caché corta por usuario y consulta; cualquier cambio del
  catálogo la invalida.
- La página inicial solicita únicamente el catálogo visible (50 filas). Panel
  ejecutivo, logística, piloto y contratos se cargan al abrir su pestaña.
- Si una integración falla, se conserva el último dato correcto con estado
  desactualizado; las acciones de cambio quedan bloqueadas.
- Las cotizaciones Envía se indexan por origen, destino, paquete, servicio y
  huella. Un pedido dividido se calcula por cada despacho real y después se
  consolida; nunca usa un promedio global como tarifa.

El contrato local enriquecido está en
`catalogo/contracts/shopify_catalog_read.graphql`. Incluye `compareAtPrice`,
`inventoryItem.unitCost`, colecciones, marca/vendor, tipo, etiquetas,
metacampos, imágenes, peso e inventario por ubicación. El comando
`import_shopify_snapshot` acepta el resultado GraphQL por entrada estándar,
es idempotente y nunca llama Shopify. Cada capacidad se clasifica como
disponible, parcial, ausente, no autorizada, no aplicable o bloqueada.

## Fase 2 — piloto operativo de solo lectura

Actualizar evidencia y persistir una ejecución masiva local:

```bash
cd work/merci-back
.venv/bin/python manage.py refresh_catalog_pilot
```

Endpoints locales comprobados con el cliente de pruebas de Django:

- `GET /api/catalogo/workspace/`: tabla, ficha, fuentes y estados.
- `GET /api/catalogo/alignment/`: conciliación paginada de Shopify, Siigo,
  Mercado Libre y Falabella, con búsqueda y estado de coincidencia.
- `GET /api/catalogo/pilot/simulation/`: cobertura, faltantes y elegibilidad.
- `GET /api/catalogo/executive/simulation/`: estimado vs realizado.
- `GET /api/catalogo/shopify/import-plan/`: contrato y puertas de escritura.

Los endpoints GET no actualizan SQLite. La evidencia se refresca únicamente
con el comando explícito anterior, evitando contención de la base local. El
importador `import_envia_snapshot` solo acepta una respuesta ya sanitizada por
entrada estándar y persiste cotización o costo realizado con huella; no llama
Envía ni crea guías.

Resultado masivo del 2026-08-24:

- 959/959 SKU Barú con costo bruto verificable e IVA incluido.
- 879 coincidencias Shopify exactas, 80 `catalog-only`, 0 ambiguas/duplicadas.
- 959 sin peso, 959 sin dimensiones y 959 con inventario pendiente.
- 0 costos Shopify verificables, 0 costos Siigo verificables y 0 cotizaciones
  Envía persistidas en esta fase.
- 959 sin regla comercial aplicable: no se inventaron margen, comisión,
  reserva ni subsidio para Barú.
- Tarifa real, $3.000, $2.000 y $0: 0 elegibles porque faltan datos logísticos
  y una política aprobada. Desconocido no se convirtió en cero.
- `externalWrites=0`.

La interfaz añade una pestaña **Piloto y fuentes** con estas métricas, una
matriz de estado por sistema/capacidad y explicación de cada bloqueo. Las
opciones de envío calculan el margen al precio actual y quedan bloqueadas si
rompen el margen mínimo o el tope de subsidio.

## Alineación multicanal actual

- Shopify es la identidad maestra y nunca se reemplaza por el catálogo de un
  proveedor o marketplace.
- La interfaz muestra una miniatura pequeña en la primera columna; usa la imagen
  Shopify o la imagen principal del canal y presenta `Sin imagen` cuando no hay
  evidencia, sin fabricar una sustitución.
- Solo un SKU exacto y único se vincula automáticamente. Duplicados, SKU
  ambiguos, códigos alternos y ausencias quedan explícitamente en revisión.
- Shopify: 3.364 variantes vigentes.
- Siigo: 5.445 filas; 2.148 coincidencias exactas, 3.281 ausentes en Shopify y
  16 ambiguas.
- Falabella: 908 publicaciones; 650 coincidencias exactas, 252 ausentes en
  Shopify y 6 ambiguas. El snapshot incluye la imagen principal disponible.
- Mercado Libre: 1.759 publicaciones leídas mediante el OAuth cifrado canónico
  de Pamo Maestro y normalizadas en 1.761 filas por sus variaciones. Resultado:
  582 coincidencias exactas, 191 ausentes en Shopify, 7 ambiguas, 2 sin SKU y
  979 filas duplicadas por SKU. Las 1.761 filas conservan miniatura.

Refresco completo, explícito y de solo lectura:

```bash
cd /Users/mauricioperez/Documents/PAMO_APP/Pamo_app_back
.venv/bin/python manage.py refresh_mercadolibre_snapshot
```

El comando ejecuta la lectura dentro del servicio autorizado de Pamo Maestro,
descarga todas las publicaciones con `search_type=scan`, completa sus detalles
por lotes y solo después reemplaza el snapshot SQLite. Si la lectura queda
vacía, incompleta, expira o falla, conserva intacto el último snapshot correcto.

## Límites y funciones no conectadas

La cotización de envío solo alimenta un estimado de checkout. La utilidad se
considera realizada únicamente tras conciliar costo real de guía, entrega,
devoluciones y ajustes. Falabella y Mercado Libre están conectados únicamente
como snapshots locales de lectura; Madecentro conserva una propuesta comercial local y Rappi permanece como
contratos futuros.

No están conectados: webhooks, sincronización incremental programada,
colecciones/metacampos completos, inventario Shopify desglosado por ubicación,
costo actual de Shopify, tarifa Envía real, ventas/guías/devoluciones reales ni
ninguna escritura hacia Shopify, Siigo o canales. Crear/sincronizar y rollback
son propuestas de interfaz y modelo; requieren autorización y pruebas futuras.

La actualización completa de Shopify sí se ejecutó mediante Admin GraphQL en
modo lectura, con credenciales inyectadas solo en memoria. Siigo y Falabella se
leyeron del mismo modo y sus resultados sanitizados se guardaron únicamente en
SQLite local. Ningún secreto fue impreso ni persistido. Envía no se consultó
para cotizaciones comerciales porque todavía faltan datos físicos canónicos y
un contrato verificable por paquete/origen.

La aceptación funcional se basó en las pruebas de backend, `manage.py check`,
validación de migraciones, lint, build del frontend y navegación autenticada
real de la tabla maestro y la alineación de canales con miniaturas.

## Costos e inventario

- Cada costo crudo conserva fuente, fecha, vigencia, IVA, descuento,
  proveedor y evidencia. La selección canónica guarda política, razón y
  discrepancias; nunca sobrescribe silenciosamente catálogo, Siigo o Shopify.
- Un SKU ausente en Siigo puede operar como `catalog-only`; crear en Siigo es
  una propuesta futura, no un error de catálogo.
- El inventario conserva fuente/proveedor/bodega, reportado, reservado, stock
  de seguridad, disponible para prometer, frescura y método de actualización.
- Solo una fuente canónica alimenta disponibilidad. Las asignaciones por canal
  consumen la misma existencia compartida; no la duplican.
- Inventario desconocido bloquea publicación y permanece `pendiente`; nunca se
  convierte en cero. Esto aplica al catálogo Barú actual.
- La lectura `/v1/products` de Siigo observada el 2026-08-24 no expone un campo
  de costo; sus `prices` se conservan como precios de lista, no como costo.
  Un costo Siigo solo será elegible cuando exista un contrato/campo verificable.

## Catálogo Barú original — importación verificable

El PDF `1-CATALOGO-PLATA-05-AGOSTO-2026.pdf`, fechado 2026-08-05, fue
importado a la SQLite local con el comando:

```bash
.venv/bin/python manage.py import_baru_catalog \
  --pdf /ruta/segura/1-CATALOGO-PLATA-05-AGOSTO-2026.pdf \
  --catalog-date 2026-08-05 \
  --tax-rate 19
```

El importador falla antes de escribir si el archivo no tiene 91 páginas o si
encuentra SKU vacíos, descripciones vacías, duplicados o precios inválidos. Los
puntos y comas se aceptan únicamente como separadores de miles: `$136.842` se
normaliza a `136842` y `$184,211` a `184211`; no se interpretan como decimales.

Resultado local verificado:

- SHA-256: `80c3a4f0ea7d6622ea0584bce7f2a1c1f0eff07ea53123a56d28481533c2b105`.
- 91 páginas, 959 filas y 959 SKU únicos.
- 0 duplicados, 0 precios inválidos, 0 SKU vacíos y 0 descripciones vacías.
- Conciliación exacta contra el snapshot Shopify local: 879 coincidencias, 80
  SKU `catalog-only` y 0 ambigüedades.
- Barú está configurado como `IVA incluido`, tasa configurable 19%. El costo
  bruto se conserva íntegro y el motor no vuelve a sumar IVA.
- El neto (`bruto / 1,19`) es un derivado auditable, no reemplaza el bruto ni
  constituye un descuento.
- Inventario, peso, dimensiones, bodega, descuentos y vigencia permanecen
  pendientes porque el PDF no los informa.

El registro `SupplierCatalogImport` conserva hash, nombre del archivo, fecha,
conteos, muestras y resultado de conciliación. Cada fila conserva página,
posición, texto original del precio, bruto, neto derivado y tratamiento fiscal.

## Evidencia de solo lectura

- Shopify: 34 páginas, 3.159 productos y 3.364 variantes; propósito: identidad,
  catálogo, imágenes, precio, estado, costo disponible e inventario por
  ubicación para persistencia local.
- Siigo: 5.445 productos; 2.148 SKU exactos con Shopify, 3.281 ausentes y 16
  ambiguos; propósito: identidad, precio de lista e inventario observado.
- Falabella: 908 publicaciones; 650 SKU exactos con Shopify, 252 ausentes y 6
  ambiguos; propósito: publicación, imagen principal, precio, estado e
  inventario cuando la API lo informó.
- Mercado Libre: 1.759 publicaciones completas, 1.761 filas normalizadas y
  1.761 miniaturas; 341 filas activas y las restantes pausadas, cerradas o en
  revisión. El snapshot se guarda solo en SQLite local.
- Railway: solo arquitectura, servicios y nombres de variables; ningún valor de
  secreto fue copiado, impreso o persistido.
- En todos los casos: `externalWrites=0`. No hubo cambios en nube, bases de
  producción, servicios, variables, despliegues, repositorios remotos o canales.
