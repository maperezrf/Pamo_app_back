# Pedidos: importación canónica local

Estado verificado el 27 de agosto de 2026 para el rango 2026-06-27 a
2026-08-27.

## Alcance y seguridad

- La fuente primaria es la API canónica de Pedidos de PAMO Maestro.
- El importador sólo consulta endpoints de pedidos y descarga de etiquetas ya
  existentes. No cotiza, no genera, no compra y no cancela guías.
- `ORDERS_EXTERNAL_READS_ENABLED=true` debe activarse sólo durante el comando
  explícito.
- `ORDERS_EXTERNAL_WRITES_ENABLED=false` es obligatorio; el comando se detiene
  si la compuerta está activa.
- La base de destino se fuerza a SQLite local. Nunca debe ejecutarse con el
  `DATABASE_URL` de Railway.
- Las asignaciones manuales de bodega y las guías manuales existentes tienen
  precedencia sobre la fuente canónica.
- Los cuatro fixtures sanitizados se conservan y se ocultan automáticamente
  cuando existen pedidos reales.
- Los PDFs se guardan en `private_uploads` y sólo se sirven mediante una sesión
  autenticada.

## Resultado observado

- 1.585 pedidos operativos.
- 1.811 despachos.
- 1.296 eventos de trazabilidad.
- 205 despachos con costo de transporte observado.
- 207 PDFs recuperados y validados por tamaño, tipo y SHA-256.
- 1.116 guías con número no entregaron un PDF recuperable: 833 respuestas 404
  y 283 respuestas 502 del servicio canónico.
- 463 pedidos sin guía.
- 198 pedidos con guía sin trazabilidad.
- 656 pedidos únicos en la revisión combinada, incluyendo un pedido que aún no
  tiene despacho creado.

Distribución del rango:

- Shopify: 977.
- Mercado Libre: 290.
- Sodimac: 317.
- Manual: 1.
- Falabella: 0 en este rango. La fuente sí registró 9 pedidos desde 2026-05-01,
  todos anteriores al rango solicitado.

## Brechas que no deben ocultarse

1. El modelo canónico de Sodimac informó 599 pedidos desde 2026-05-01, pero su
   última actualización de origen quedó registrada el 2026-08-08. La lectura
   local no crea un segundo consumidor y no reinyecta órdenes.
2. En una muestra controlada de diez guías Shopify sin PDF, Envía encontró los
   diez pedidos, pero ninguno tenía todavía `shipment_id`. La API oficial exige
   ese identificador para descargar la etiqueta; no se debe sustituir por la
   etiqueta de otro despacho.
3. Mercado Libre sólo ofrece la etiqueta cuando el envío conserva un estado
   imprimible. Los errores remotos se conservan como pendientes, no como PDFs
   válidos.

## Comando operativo

El comando se ejecuta con las credenciales inyectadas en memoria por Railway.
No se deben copiar tokens a archivos ni a la terminal.

```text
python manage.py import_pamo_orders --from YYYY-MM-DD --to YYYY-MM-DD --workers 8
python manage.py import_pamo_orders --from YYYY-MM-DD --to YYYY-MM-DD --labels-only --label-workers 5
```

Antes de ejecutar, se deben establecer explícitamente la API canónica, la
lectura externa y la base local; las escrituras externas deben permanecer
apagadas.

## Actualización automática local

El planificador local ejecuta una lectura idempotente cada cinco minutos sin
consultar directamente Shopify, Mercado Libre, Sodimac, Falabella o Envía. Su
única fuente es la API canónica de Pedidos, por lo que no crea un segundo
consumidor de los canales.

```text
python manage.py run_orders_sync_scheduler --loop --interval-seconds 300
```

- Cada ciclo normal vuelve a leer los dos días más recientes para absorber
  pedidos, cambios de estado, guías y PDFs que aparezcan con retraso.
- Cada 72 ciclos ejecuta una recuperación de 14 días. La recuperación histórica
  de hasta 93 días permanece como comando manual para evitar miles de consultas
  repetidas.
- Un bloqueo exclusivo impide dos planificadores sobre la misma SQLite.
- Si la fuente falla, los datos anteriores permanecen visibles y el estado se
  marca como desactualizado sin vaciar la tabla.
- El proceso se detiene antes de leer si la base no es SQLite local o si alguna
  compuerta de escritura externa está habilitada.

## Siguiente fase: cotización y generación

La cotización y generación de guías no está habilitada por esta importación.
El diseño aprobado para esa fase debe:

1. Cotizar servicios elegibles usando peso, dimensiones, destino y SLA.
2. Mostrar la alternativa elegible más económica, sin seleccionarla de forma
   irreversible.
3. Mantener elección humana por despacho y permitir una cola masiva.
4. Guardar la elección humana como preferencia auditable, no como regla rígida.
5. Exigir idempotencia por despacho y una confirmación separada antes de llamar
   al endpoint que genera y puede cobrar una guía.
