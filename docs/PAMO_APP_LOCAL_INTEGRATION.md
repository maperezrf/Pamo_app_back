# PAMO APP local integrado: Catalogo + Pedidos

## Alcance

Esta rama integra el modulo local de Pedidos dentro del mismo backend de PAMO
APP que ya contiene Catalogo multicanal. No reemplaza los prototipos separados
ni publica cambios en Beta o Produccion.

- Rama: `integration/pamo-app-local-20260827`
- Backend integrado: `http://127.0.0.1:8013`
- Frontend integrado: `http://127.0.0.1:5176`
- Persistencia: copia SQLite local independiente
- Integraciones externas: desactivadas
- Escrituras externas: `externalWrites=0`

Los servicios preexistentes en 5173/8010, 5174/8011 y 5175/8012 no deben
cerrarse ni reutilizarse durante esta validacion.

## Datos y recuperacion

La base integrada parte de una copia del catalogo local validado. Las tablas de
Pedidos se agregan con una migracion aditiva y se cargan solo cuatro pedidos
sanitizados mediante `seed_orders_local`. La copia anterior a la migracion se
conserva localmente en `.backups/` y no se versiona.

El usuario local recibe solamente los grupos `Catalogo` y `Operaciones`; no es
administrador. La apertura de sesion local requiere simultaneamente
`DEBUG=True` y `LOCAL_DEMO_AUTH_ENABLED=True`.

## Arranque controlado

Usar la misma instalacion Python y las mismas dependencias Node ya existentes;
no instalar paquetes ni liberar puertos. Las variables se inyectan al proceso y
no se guardan secretos en archivos.

Backend, desde este worktree:

```bash
/usr/bin/env SECRET_KEY=integration-local-only DEBUG=True \
  GOOGLE_CLIENT_ID=integration.invalid.apps.googleusercontent.com \
  GITHUB_WEBHOOK_SECRET=integration-disabled MCP_API_KEY=integration-disabled \
  DATABASE_URL= EXTERNAL_WRITES_ENABLED=False SIIGO_INVOICE_WRITES_ENABLED=False \
  SHOPIFY_READS_ENABLED=False ORDERS_LOCAL_MODE=True \
  ORDERS_EXTERNAL_READS_ENABLED=False ORDERS_EXTERNAL_WRITES_ENABLED=False \
  LOCAL_DEMO_AUTH_ENABLED=True \
  CORS_ALLOWED_ORIGINS=http://127.0.0.1:5176 \
  CSRF_TRUSTED_ORIGINS=http://127.0.0.1:5176 \
  /Users/mauricioperez/Documents/PAMO_APP/Pamo_app_back/.venv/bin/python \
  manage.py runserver 127.0.0.1:8013
```

Frontend, desde su worktree integrado:

```bash
/usr/bin/env VITE_API_BASE_URL=http://127.0.0.1:8013 \
  VITE_LOCAL_DEMO_AUTH=true npm run dev -- --host 127.0.0.1 --port 5176
```

## Puertas de aprobacion

Antes de considerar una integracion remota deben aprobarse:

1. pruebas backend de `catalogo`, `pedidos`, `accounts` y `feature_tracking`;
2. lint y compilacion frontend;
3. navegacion autenticada Catalogo -> Pedidos y persistencia tras recargar;
4. responsive movil y ausencia de errores inesperados en consola/red;
5. confirmacion de `externalWrites=0`;
6. aprobacion separada para push, PR, Beta o Produccion.

