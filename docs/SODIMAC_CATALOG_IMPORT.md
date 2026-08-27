# Contrato local de catálogo Sodimac / Homecenter

## Límite de la fase

Este módulo no usa una API completa de catálogo porque esa capacidad no está disponible. El archivo entregado por Sodimac/Homecenter es la fuente de identidad; Shopify local/snapshot conserva el SKU canónico. Una página pública solo puede aportar evidencia cambiante de contenido y requiere aprobación jurídica/técnica antes de automatizarse.

- Entorno: SQLite local.
- Escrituras externas: `0`.
- Beta, Railway, Shopify y Sodimac: sin cambios.
- API de inventario: adaptador separado y desconectado.
- API de pedidos: adaptador separado y desconectado; una venta no demuestra que la ficha esté completa.

## Fuentes locales cargadas el 26 de agosto de 2026

Los originales se preservan sin modificaciones en
`/Users/mauricioperez/Documents/PAMO_APP/_local_sources/sodimac/2026-08-26/`.

### Publicaciones: `productos_sodimac.xlsx`

El contrato real del archivo es:

- `sku_pamo`: SKU canónico de PAMO que debe coincidir exactamente con una variante local de Shopify.
- `sku_sodimac`: identidad propia de la publicación en Sodimac.
- `ean`: código de barras cuando está disponible.

Resultado local actual:

- 1.058 relaciones examinadas.
- 711 publicaciones vinculadas por SKU exacto.
- 337 SKU PAMO ausentes en el snapshot local.
- 10 SKU PAMO ambiguos, con más de una variante candidata.
- Estado del lote: `APPLIED_PARTIAL`; solo se aplicaron las filas exactas.

Ausente no significa inexistente en Shopify: significa que no está demostrado en el
snapshot local actual. Ambiguo nunca se resuelve por título, EAN o aproximación.

### Recetas: `kits_sodimac.xlsx`

Un kit no es una publicación plana ni un SKU inventado. Se conserva como una receta:

- `kitnumber`: SKU propio del kit en Sodimac.
- `ean`: EAN del kit, si el canal lo suministra.
- `sku`: SKU PAMO del componente.
- `quantity`: unidades de ese componente dentro del kit.

La receta permite varias referencias distintas, varias unidades de un mismo SKU y
cualquier combinación de ambas. Si un componente se repite en varias filas del mismo
kit, sus cantidades se suman de forma determinista.

Resultado local actual:

- 108 kits y 288 componentes.
- 77 kits con todos sus componentes identificados.
- 31 kits en revisión.
- 248 componentes exactos, 26 ausentes y 14 ambiguos.

El `kitnumber` puede existir únicamente en Sodimac. No se exige crear un producto base
en Shopify para reconocer la receta; el vínculo canónico del kit se conserva solamente
cuando está explícitamente demostrado en `productos_sodimac.xlsx`.

## Cálculos de kits

Los valores se derivan en tiempo de consulta desde los componentes canónicos, para no
copiar costos o inventarios que después queden obsoletos:

- Costo del kit = suma de `cantidad × costo canónico unitario`.
- Precio de referencia = suma de `cantidad × precio actual de la variante`.
- Unidades posibles = mínimo de `piso(ATP del componente / cantidad requerida)`.

Un costo, precio, inventario o componente faltante bloquea el total correspondiente;
nunca se reemplaza por cero. Actualmente 21 kits tienen costo completo y 76 tienen
inventario completo en la evidencia local.

El precio de referencia **no es el precio de venta de Sodimac**. El precio final queda
vacío hasta aprobar una política comercial versionada que incluya comisión del canal,
impuestos, logística, margen mínimo y redondeo. Esto evita publicar una cifra que parezca
precisa pero sea comercialmente incorrecta.

## Archivos CSV/XLSX genéricos

Campos requeridos:

- `sku_shopify` o `sku_pamo`: SKU canónico exacto.
- `sku_sodimac`: SKU Sodimac/Homecenter exacto.

Campos opcionales:

- `listing_id`, `url_sodimac`, `titulo_sodimac`, `marca_sodimac`, `descripcion_sodimac`.
- `imagenes_urls`: URLs separadas por `|`, `;` o salto de línea, conservando el orden.
- `atributos_json`, `estado_publicacion`, `inventario`, `fuente_inventario`.
- `proveedor`, `bodega`, `fecha_archivo`, `ultima_verificacion`.

El mapeo de encabezados es configurable en la interfaz. La vista previa conserva nombre, tamaño, SHA-256, mapeo y filas normalizadas; no almacena el binario completo ni datos sensibles innecesarios. El fingerprint combina checksum y mapeo para que el mismo archivo pueda revisarse de forma idempotente bajo un contrato explícito.

## Conflictos y precedencia

- Un SKU canónico con una sola variante exacta puede crear `LINKED_EXACT`.
- Un mismo SKU Sodimac apuntando a varios SKU canónicos queda `AMBIGUOUS` y permanece en revisión.
- Un SKU canónico ausente queda rechazado; no se busca por título, marca o imagen.
- Las filas duplicadas se reportan y no crean vínculos adicionales.
- Las decisiones con `manual_decision=true` nunca se sobrescriben.
- Un SKU Shopify puede tener varias publicaciones Sodimac cuando cada relación tiene identidad explícita.
- En un lote parcial, las coincidencias exactas se aplican y lo no resuelto queda visible sin bloquearlas.

Estados de vínculo: `UNLINKED`, `LINKED_EXACT`, `AMBIGUOUS`, `STALE`, `NOT_FOUND`, `NEEDS_REVIEW`.

## Evidencia y puntuación

Clases: `CONFIRMED_BY_FILE`, `CONFIRMED_BY_API`, `OBSERVED_PUBLIC_PAGE`, `INFERRED`, `UNKNOWN`.

Pesos explicables:

| Dimensión | Peso |
| --- | ---: |
| Identidad | 25 |
| Título y marca | 20 |
| Imágenes, orden y principal | 20 |
| Descripción | 15 |
| Atributos | 10 |
| Disponibilidad/inventario | 10 |

`APPROVED` requiere 85 o más y ningún bloqueo; `WARNING` cubre 60–84; `BLOCKER` aplica por debajo de 60 o cuando falta identidad/listing. La similitud textual solo explica. La similitud visual queda `UNKNOWN` sin hash o mecanismo oficial; nunca crea un vínculo.

## Cola diaria futura

`ENQUEUE_INCREMENTAL_LOCAL` prepara únicamente tareas vencidas, cambiadas, críticas o prioritarias. Usa fingerprint por publicación, cache, máximo tres intentos, backoff y fallback manual/archivo. No ejecuta red, cron, CAPTCHA, login ni bypass.

Antes de automatizar páginas públicas se requiere:

1. Validación jurídica de términos y robots.
2. Contrato técnico de búsqueda/URL y límites de tasa.
3. Identidad de agente, cache y retención de evidencia aprobadas.
4. Manejo explícito de CAPTCHA, bloqueos o inestabilidad mediante tarea manual, nunca evasión.

## Reversión

La reversión local desactiva y marca `STALE` los vínculos creados por el lote. Conserva el historial y no toca vínculos manuales. La reversión de kits desactiva el snapshot nuevo y reactiva el anterior. Para volver completamente al punto inicial, detener el backend y restaurar `/Users/mauricioperez/Documents/PAMO_APP/Pamo_app_back/.backups/db.sqlite3.pre-sodimac-real-import-20260826` después de respaldar la base vigente.
