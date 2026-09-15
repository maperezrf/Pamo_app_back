# Índice técnico del backend

Este documento es el punto de entrada para entender y modificar el backend
de Pamo. Describe cómo encontrar el contexto necesario; no reemplaza el
código como evidencia del comportamiento ejecutable.

## Propósito y límites

Este repositorio implementa la API de Pamo con Django y Django REST
Framework. Es dueño de la lógica de negocio, la persistencia, autenticación
y autorización, y las integraciones con proveedores externos. El frontend
es un repositorio separado y consume contratos publicados por este backend.

`pamo-one-engineering` es una fuente histórica de módulos. No es la fuente
de verdad de la implementación que vive aquí: antes de reutilizar una pieza
de allí, se debe revisar el contexto de este repositorio y adaptar solo lo
que corresponda.

## Lectura obligatoria antes de cambiar código

Arquitecto y desarrollador siguen este orden:

1. Leer este índice completo.
2. Leer las instrucciones del repositorio y los documentos aplicables al
   tipo de cambio.
3. Leer el expediente de la app, patrón y contrato implicados cuando estén
   disponibles.
4. Buscar una capacidad reutilizable documentada antes de proponer o crear
   una nueva.
5. Solo entonces inspeccionar código para validar una afirmación documental,
   localizar la implementación exacta o resolver un vacío concreto.

No se debe explorar el código al azar como sustituto de este contexto. Si la
documentación y el código discrepan, se verifica el comportamiento real y se
actualiza la documentación pertinente en el mismo cambio cuando corresponda.

## Memoria compartida

El conocimiento durable del proyecto vive exclusivamente en este repositorio,
especialmente en `docs/`. La memoria privada de una herramienta, su historial
de conversación o archivos locales de un IDE no pueden ser requisito para
entender, planear o modificar el sistema. Un hallazgo útil de esas fuentes se
contrasta y, si aplica, se incorpora a la documentación versionada.

## Mapa actual

| Área | Ubicación | Responsabilidad conocida | Documentación detallada |
| --- | --- | --- | --- |
| Configuración | `config/` | Settings, URLs raíz y constantes de entorno. | Pendiente |
| Accesos y seguridad | `accounts/` | Login con Google, correos permitidos, roles y permisos reutilizables. | [`apps/accounts.md`](apps/accounts.md) |
| Integraciones | `integrations/` | Transporte y estado de conexión con proveedores externos. | [`apps/integrations.md`](apps/integrations.md) |
| Orquestador de procesos | `orchestrator/` | Ejecución/programación en segundo plano de procesos de negocio registrados por su app dueña. | [`apps/orchestrator.md`](apps/orchestrator.md) |
| Directorio de clientes de Shopify | `customers/` | Tabla local sincronizada con Shopify (webhook + reconciliación); resuelve "¿existe este cliente?" por cédula. | [`apps/customers.md`](apps/customers.md) |
| Contabilidad | `accounting/` | Área de negocio contable. | Pendiente |
| Facturación | `facturacion/` | Área de negocio de facturación. | Pendiente |
| Logística | `logistics/` | Área de negocio logística. | Pendiente |
| Seguimiento de funcionalidades | `feature_tracking/` | Registro y ciclo de vida de funcionalidades. | Pendiente; su estado actual debe validarse antes de intervenir. |

Las carpetas de proveedor dentro de `integrations/` se documentarán como
parte del expediente de Integraciones; cada una conserva únicamente lo que
necesita hoy, sin asumir que todos los proveedores tienen la misma forma.

## Documentos disponibles

Los documentos se agregan cuando existe una capacidad real y han sido
contrastados con la implementación. No se crean archivos vacíos solo para
completar una estructura.

| Tema | Documento previsto | Cuándo leerlo |
| --- | --- | --- |
| Límites y dependencias de las apps | [`architecture/APP_BOUNDARIES.md`](architecture/APP_BOUNDARIES.md) | Al crear, mover o conectar funcionalidad entre áreas. |
| Integraciones externas | [`architecture/INTEGRATIONS.md`](architecture/INTEGRATIONS.md) | Al agregar o modificar un proveedor, webhook, sincronización o cliente. |
| Autorización | [`patterns/AUTHORIZATION.md`](patterns/AUTHORIZATION.md) | Al proteger una vista o reutilizar un mixin de permisos. |
| Clientes externos | [`patterns/EXTERNAL_CLIENTS.md`](patterns/EXTERNAL_CLIENTS.md) | Al hacer solicitudes a un proveedor externo. |
| Webhooks de proveedor | [`patterns/PROVIDER_WEBHOOKS.md`](patterns/PROVIDER_WEBHOOKS.md) | Al recibir un evento entrante de un proveedor externo (Shopify, WhatsApp, etc.). |
| Guía del orquestador de procesos | [`patterns/orchestrator-usage-guide.md`](patterns/orchestrator-usage-guide.md) | Al registrar, lanzar o programar un proceso en segundo plano. |
| API, vistas y serializers | [`patterns/API_VIEWS_AND_SERIALIZERS.md`](patterns/API_VIEWS_AND_SERIALIZERS.md) | Al crear o modificar endpoints. |
| Contrato de API | [`contracts/API.md`](contracts/API.md) | Al cambiar el contrato que consume el frontend. |
| Expedientes de apps | `docs/apps/<app>.md` | Siempre que la tarea afecte la app respectiva. |

## Qué merece documentación

Se actualiza o crea documentación en el mismo cambio si este introduce,
modifica o depreca una capacidad que:

- puede reutilizarse en más de una app o funcionalidad;
- es un mixin, decorador, helper, cliente externo, servicio, serializer,
  componente de infraestructura o contrato;
- contiene reglas de permisos, idempotencia, errores, seguridad o negocio
  que no son evidentes;
- afecta la interfaz acordada con el frontend o un proveedor externo.

Cambios locales, obvios y sin posibilidad razonable de reutilización no
requieren un documento nuevo. Cuando haya duda, se actualiza el expediente
de la app afectada en lugar de crear un patrón artificial.

## Regla de mantenimiento

Todo documento técnico debe indicar el propósito, ubicación real del código,
forma de uso, límites y pruebas relevantes de la capacidad que describe.
Nunca incluye valores de secretos, tokens, cookies ni datos sensibles.

El desarrollador no declara una tarea terminada hasta haber comprobado si el
cambio exige actualizar el patrón, el expediente de la app o el contrato
correspondiente.

## Roles de trabajo

- [Arquitecto](roles/ARCHITECT.md): analiza el requerimiento y entrega un
  plan reutilizable, sin implementar código.
- [Desarrollador](roles/DEVELOPER.md): verifica y ejecuta el plan, prueba el
  resultado y actualiza la documentación que el cambio amerite.
