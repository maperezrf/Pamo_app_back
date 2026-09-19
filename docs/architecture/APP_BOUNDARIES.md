# Límites de apps

Cada app posee su propio concepto de negocio. Una capacidad nueva se ubica
en el área dueña del dato y de sus reglas, no en la app que la consume.

## Límites actuales

| Área | App o ubicación | Responsabilidad |
| --- | --- | --- |
| Configuración | `config/` | Settings, URLs raíz y lectura centralizada de entorno. |
| Accesos | `accounts/` | Sesión Google, correos habilitados, roles y menú visible. |
| Integraciones | `integrations/` | Transporte hacia proveedores y estado técnico de conexión. |
| Orquestador de procesos | `orchestrator/` | Ejecución/programación en segundo plano de procesos de negocio ya registrados por su app dueña; no conoce lógica de negocio ni persiste sus datos. |
| Directorio de clientes de Shopify | `customers/` | Tabla local sincronizada con los clientes de Shopify (webhook + reconciliación); resuelve "¿existe este cliente?" por cédula para cualquier canal, porque Shopify no permite buscarlo en vivo por ese campo. |
| Importación de pedidos de marketplace | `orders/` | Orquesta traer pedidos de un marketplace (Falabella y a futuro otros) y crearlos en Shopify, combinando `integrations/<provider>/` y `customers/`. No es transporte de proveedor ni dueña del directorio de clientes. |
| Contabilidad | `accounting/` | Reglas y datos contables. |
| Facturación | `facturacion/` | Reglas y datos de facturación. |
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
