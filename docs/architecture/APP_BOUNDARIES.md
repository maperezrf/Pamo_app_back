# Límites de apps

Cada app posee su propio concepto de negocio. Una capacidad nueva se ubica
en el área dueña del dato y de sus reglas, no en la app que la consume.

## Límites actuales

| Área | App o ubicación | Responsabilidad |
| --- | --- | --- |
| Configuración | `config/` | Settings, URLs raíz y lectura centralizada de entorno. |
| Accesos | `accounts/` | Sesión Google, correos habilitados, roles y menú visible. |
| Integraciones | `integrations/` | Transporte hacia proveedores y estado técnico de conexión. |
| Orquestador de procesos | `orchestrator/` | Ejecución/programación en segundo plano de procesos de negocio. Todos los procesos se registran en `orchestrator/registrations.py`, único punto que importa la función pública de la app dueña (`<app>/functions/`); no conoce la lógica de negocio ni persiste sus datos. |
| Directorio de clientes de Shopify | `customers/` | Tabla local sincronizada con los clientes de Shopify (webhook + reconciliación); resuelve "¿existe este cliente?" por cédula para cualquier canal, porque Shopify no permite buscarlo en vivo por ese campo. |
| Importación de pedidos de marketplace | `orders/` | Orquesta traer pedidos de un marketplace (Falabella por lote programado, Mercado Libre por webhook) y crearlos en Shopify a nombre de un cliente fijo por canal, usando `integrations/<provider>/`. Guarda los datos del comprador para facturar. Mantiene una copia local de los pedidos de Shopify (webhook + reconciliación) y expone al frontend el listado de ventas desde esa copia, con el comprador real y los pedidos que no se pudieron crear. Es dueña del flujo completo del pedido, desde la creación hasta el despacho y el tracking: asigna la bodega y avisa a cada bodega por su canal (decisión del 2026-10-07, ver `docs/implementations-plans/order-dispatch-to-warehouses.md`); por eso no se crea `logistics` para el despacho. No es transporte de proveedor ni dueña del directorio de clientes. |
| Catálogo de productos | `products/` | Productos de Pamo, kits (componentes con cantidad) y equivalencias de SKU por marketplace; traduce el SKU que reporta un canal a lo que va a Shopify. Dueña de la lista `Marketplace`. No depende de `orders`. |
| Contabilidad | `accounting/` | Reglas y datos contables. |
| Facturación | `invoicing/` | Facturas en Siigo de pedidos de marketplace y las reglas para calcularlas (hoy Sodimac). Depende de `orders` (lee `MarketplaceOrder`), de `products` (reparto de kits) y de `integrations.siigo`. `orders` no importa `invoicing`. |
| Logística | `logistics/` | Reglas y datos logísticos. |

## Regla para proveedores

`integrations/<provider>/` puede contener cliente, constantes no secretas,
consultas, funciones de adaptación, pruebas y estado de conexión. No guarda
datos de negocio como pedidos, facturas o productos: esos datos pertenecen a
la app de área dueña. Una integración puede llamar a una app de área para
normalizar y persistir; un área no importa modelos internos del proveedor.

Antes de crear una app, confirmar que el concepto no pertenece a una de las
áreas anteriores. Documentar el nuevo límite en este archivo y crear su
expediente en `docs/apps/` cuando sea una nueva responsabilidad estable.
