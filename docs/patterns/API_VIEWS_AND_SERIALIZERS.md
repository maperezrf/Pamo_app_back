# Patrón: APIViews y contrato HTTP

`config/urls.py` registra las rutas globales y monta las rutas de cada app;
estas viven normalmente en `<app>/urls.py`. Los endpoints de API se
implementan habitualmente como `APIView` y declaran permisos explícitos o
heredan el permiso predeterminado de sesión autenticada. Una vista Django
basada en función se permite cuando el caso es puntual, como la entrega del
token CSRF en `accounts.views.csrf`.

## Reglas

- Los `APIView` de una app viven en `<app>/apis.py`, no en `views.py`
  (ejemplos: `orchestrator/apis.py`, `orders/apis.py`,
  `products/apis.py`). `views.py` queda como boilerplate de `startapp` o
  no existe. Los webhooks de proveedor van en `<app>/webhooks.py` (ver
  [`PROVIDER_WEBHOOKS.md`](PROVIDER_WEBHOOKS.md)). `accounts/views.py` es
  anterior a esta regla.
- Mantener la lógica de negocio fuera de la vista: delegar en `functions/`
  o en la app dueña del concepto.
- Declarar el permiso de cada endpoint. Una excepción pública debe usar
  `AllowAny` explícitamente.
- Para entidades expuestas, usar serializers de DRF y concentrar allí la
  validación de entrada. Actualmente las rutas de acceso devuelven respuestas
  pequeñas construidas directamente; no es un precedente para serializar
  entidades complejas a mano.
- Al cambiar una ruta, método, permiso, body, respuesta o estado HTTP,
  actualizar [`../contracts/API.md`](../contracts/API.md) en el mismo cambio.
- Para sesiones entre frontend y backend, mantener CORS con credenciales y
  configurar orígenes/CSRF desde constantes de entorno. En producción, las
  cookies requieren transporte seguro y `SameSite=None` cuando los hosts son
  distintos.

## Pruebas

Cada endpoint nuevo que toque modelos o proveedores requiere prueba de camino
feliz y rechazo por permisos. Ejecutar `python manage.py check` al cerrar un
cambio de API.
