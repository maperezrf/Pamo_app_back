# Plan: pedidos de Falabella con cliente fijo en Shopify

Estado: **implementado (2026-09-23)**, pruebas en verde salvo
`test_process_registered_in_orchestrator_registry` (falla ajena a este
cambio, ver `docs/apps/orders.md` § Pruebas). Pendiente: `migrate` de
`orders/migrations/0003_buyer_billing_fields.py` en Railway, configurar
`FALABELLA_SHOPIFY_CUSTOMER_ID` en Railway y la verificación en vivo.
Llaves de `AddressBilling` confirmadas contra 5 pedidos reales el
2026-09-23 (`Address1..5`, `City`, `Ward`, `Region`, `PostCode`, `Phone`,
`Phone2`; teléfono vacío en los 5). Modifica el flujo descrito en
[`marketplace-orders-import.md`](marketplace-orders-import.md) (app
`orders`).

## Objetivo

Todos los pedidos de Falabella se crean en Shopify a nombre de **un único
cliente fijo** (creado a mano en Shopify por el equipo). El importador deja
de buscar/crear clientes en Shopify y deja de reconciliar el directorio de
`customers` en cada corrida. Los datos reales del comprador se guardan
completos en `MarketplaceOrder` para usarlos después al facturar en Siigo
(la facturación **no** es parte de este cambio).

## Decisiones tomadas

1. **Cliente fijo** configurable por entorno:
   `FALABELLA_SHOPIFY_CUSTOMER_ID` (id numérico, sin `gid://`), leída vía
   `config/constants.py`. El id cambia entre tiendas/entornos, por eso no
   va fijo en código.
2. **Se eliminan del flujo** `reconcile_from_shopify`,
   `find_by_identification`, `create_customer`, `create_customer_address` y
   `upsert_from_shopify_customer`. `orders` deja de depender de
   `customers`. La app `customers` no cambia (webhook y reconciliación
   nocturna siguen existiendo por su cuenta).
3. **Datos del comprador para facturación**: además de lo que ya se guarda
   (cédula, nombre, apellido, email), se guardan dirección, ciudad,
   departamento y teléfono de facturación. Confirmado por el usuario que
   dirección, ciudad y cédula vienen en la orden de Falabella.
4. **Tipo de persona**: siempre persona natural para compradores de
   Falabella. Es una regla de la futura capa de facturación, no se
   persiste en `MarketplaceOrder`.
5. **Trazabilidad en Shopify**: el `note` de la orden incluye comprador y
   cédula, porque en Shopify todas las órdenes muestran el mismo cliente:
   `"Falabella #<numero> — <nombre> <apellido> CC <cedula>"`.

## Estado actual verificado

- `orders/functions/import_falabella_orders.py`: etapa 1 es
  `reconcile_from_shopify()` (pagina ~19.600 clientes, ~160 s).
- `orders/functions/process_pending_orders.py::_process_one_order`: busca
  al comprador localmente y, si no existe, lo crea en Shopify
  (`_create_shopify_customer`).
- `integrations/falabella/functions/get_orders.py::_normalize_order`: de
  `AddressBilling` solo extrae `CustomerEmail`; la cédula sale de
  `NationalRegistrationNumber` a nivel de orden.

## Cambios

### 1. `config/constants.py` y `.env.example`

```python
# Cliente fijo de Shopify al que se asignan todos los pedidos de Falabella
# (app orders). Id numérico, sin prefijo gid://.
FALABELLA_SHOPIFY_CUSTOMER_ID = config("FALABELLA_SHOPIFY_CUSTOMER_ID", default="")
```

`.env.example`: agregar la variable sin valor.

### 2. `integrations/falabella/functions/get_orders.py`

Ampliar `_normalize_order` con los datos de facturación del comprador:
`customer_address`, `customer_city`, `customer_region`, `customer_phone`.

**Vacío a verificar por el desarrollador**: los nombres exactos de las
llaves dentro de `AddressBilling` no están confirmados en este repositorio
(la única prueba simula solo `CustomerEmail`). Según la API de Seller
Center se esperan `Address1`…`Address5`, `City`, `Region`, `Phone`,
`Phone2`. Confirmarlos contra una respuesta real de `GetOrders` antes de
implementar y dejar la fecha de verificación en el docstring, igual que
los demás campos. Si la dirección viene partida en `Address1..N`, unir las
partes no vacías con espacio. Campo ausente → `""` (misma regla actual:
decidir qué hacer con datos incompletos no es responsabilidad del
normalizador).

Actualizar `integrations/falabella/tests.py` con un `AddressBilling`
completo en el fixture.

### 3. `orders/models.py` + migración

Agregar a `MarketplaceOrder` (todos `blank=True`):

```python
customer_address = models.CharField(max_length=255, blank=True)
customer_city = models.CharField(max_length=100, blank=True)
customer_region = models.CharField(max_length=100, blank=True)
customer_phone = models.CharField(max_length=32, blank=True)
```

Actualizar el comentario de los campos `customer_*`: ya no sirven para
crear el cliente en Shopify, sino como fuente para la factura en Siigo.

`Status.ERROR_CUSTOMER` **se conserva** (hay filas históricas con ese
valor) y se documenta como obsoleto: ningún código nuevo lo asigna. Esas
filas no tienen `shopify_order_id`, así que la siguiente corrida las
reintenta sola con el cliente fijo — no hace falta migración de datos.

`python manage.py makemigrations orders`.

### 4. `orders/functions/fetch_falabella_orders.py`

Pasar los cuatro campos nuevos en `defaults` del `get_or_create`. Pedidos
ya existentes no se actualizan (regla actual); los datos de facturación de
pedidos viejos quedan vacíos — aceptable, se pueden recuperar de Falabella
si alguno se llega a facturar.

### 5. `orders/functions/process_pending_orders.py`

- Quitar imports de `customers.*`, `create_customer`,
  `create_customer_address` y la función `_create_shopify_customer`.
- Al inicio de `process_pending_orders`, leer
  `FALABELLA_SHOPIFY_CUSTOMER_ID`; si está vacía, lanzar `ValueError`
  antes de tocar cualquier pedido (el orquestador marca la ejecución como
  `ERROR`; ningún pedido queda mal marcado).
- `_process_one_order(order, customer_id)`: resolver SKUs → `create_order(
  customer_id=customer_id, note=<ver decisión 5>, ...)` → guardar
  `shopify_customer_id=customer_id` junto al resto.

### 6. `orders/functions/import_falabella_orders.py`

Quitar la etapa de `reconcile_from_shopify` y el import. Quedan dos
etapas: fetch (0→40 %) y process (40→100 %). Actualizar el docstring.

## Pruebas

`orders/tests.py`:
- Eliminar `test_creates_a_new_shopify_customer_when_not_found_locally`,
  `test_marks_error_creando_cliente_when_customer_data_is_insufficient` y
  los mocks de `find_by_identification` / `reconcile_from_shopify`.
- Nuevas:
  - la orden se crea con el id fijo y el `note` incluye nombre y cédula;
  - sin `FALABELLA_SHOPIFY_CUSTOMER_ID`, el proceso lanza error y ningún
    pedido cambia de estado;
  - un pedido en `error_creando_cliente` se procesa y queda `orden_creada`;
  - `fetch_falabella_orders` guarda dirección, ciudad, departamento y
    teléfono;
  - `import_falabella_orders` llama solo a fetch y process.

`integrations/falabella/tests.py`: normalización de los campos nuevos y de
un `AddressBilling` ausente.

Ejecutar `python manage.py test orders integrations.falabella` y
`python manage.py check`.

## Verificación en vivo

1. Crear el cliente fijo en Shopify y configurar
   `FALABELLA_SHOPIFY_CUSTOMER_ID` (local y Railway).
2. `import_falabella_orders(params={"limit": 1})` contra la cuenta real.
3. Confirmar en Shopify la orden con el cliente fijo y el `note`, y en
   `/admin/` que el `MarketplaceOrder` tiene los datos de facturación.

## Riesgos

- Nombres de campos de `AddressBilling` sin verificar (ver cambio 2).
- Riesgo de orden duplicada por fallo de red en `create_order`: sin
  cambios, sigue abierto (ver `marketplace-orders-import.md`).

## Fuera de alcance (para la fase de facturación)

- Armar el `customer` de Siigo desde `MarketplaceOrder`: `person_type`
  persona natural (decisión 4), `id_type` cédula de ciudadanía, código de
  ciudad/departamento en el formato que exige Siigo (probablemente requiere
  mapear el texto de Falabella a códigos DANE — sin verificar).
- Prueba en vivo de `create_invoice` (pendiente de revisar reglas de
  facturación).
- La orden de Falabella trae además `ExtraBillingAttributes` (`LegalId`,
  `FiscalPerson`, `DocumentType`, `ReceiverLegalName`, …) y un indicador
  `InvoiceRequired`. En los pedidos revisados venían vacíos / `"false"`;
  evaluarlos al diseñar la facturación (p. ej. compradores empresa con
  NIT).

## Documentación a actualizar en el mismo cambio

- `docs/apps/orders.md`: dos etapas, cliente fijo, campos de facturación,
  `error_creando_cliente` obsoleto, pruebas.
- `docs/implementations-plans/marketplace-orders-import.md`: nota que
  apunta a este plan (reemplaza la decisión 6 y las filas de cliente en la
  tabla de reutilización).
- `docs/architecture/APP_BOUNDARIES.md`: fila `orders` sin "y
  `customers/`".
- `docs/apps/customers.md`: `orders`/Falabella ya no lo consume.
- Docstring de `get_orders` con los campos nuevos y su fecha de
  verificación.
