# CLAUDE.md — Backend (Pamo_app_back)

Guía para Claude Code (o cualquier agente/IDE) al trabajar en este
repositorio: **Django 5.2.17 + Django REST Framework 3.18** para **Pamo**.
Este repo es independiente de `pamo_app_front` (frontend React) y del repo
del servidor MCP `governance-pamo` — cada uno tiene su propio ciclo de vida
y su propio `CLAUDE.md`.

## Consulta obligatoria de lineamientos (MCP `governance-pamo`)

**Regla no negociable:** ante cualquier pedido de código — nuevo o
modificación, sin importar lo simple o urgente que parezca — lo primero
que se hace, antes de leer el código existente, antes de proponer un plan
y antes de escribir una sola línea, es consultar el servidor MCP
`governance-pamo`. No resolver el pedido por cuenta propia usando solo el
conocimiento general del modelo sin haber pasado por este paso primero. Si
todavía no se consultó el MCP en la conversación actual, hacerlo ahora
antes de continuar.

Consultar el servidor MCP `governance-pamo` en este orden:

1. `obtener_mapa_documentacion` — índice de toda la documentación.
2. `obtener_lineamientos_generales` — siempre.
3. `obtener_lineamientos_backend` — arquitectura Django/DRF: apps por
   área, vistas y permisos, pool de integraciones externas, secretos,
   modelos/migraciones/Celery, contrato con el frontend, ciclo de vida de
   un feature (prototype → QA → producción), testing, checklist.
4. `obtener_lineamientos_git` — al ramear, commitear o abrir un PR.

Si la tarea no está cubierta por ninguno de estos documentos, seguir el
conocimiento general del modelo y las convenciones ya presentes en el
código — no bloquear el trabajo por falta de lineamiento explícito.

`docs/GOVERNANCE.md` en este repo es solo un puntero corto a lo de arriba,
no un documento a mantener en paralelo.

## Stack

- Python, Django 5.2.17 + Django REST Framework 3.18.
- Base de datos: SQLite en local (`db.sqlite3`); Postgres pendiente de
  decidir para producción.
- Auth: Google OAuth (`google-auth`) + sesión de Django por cookie (no
  JWT).
- Config: `python-decouple`, todo en `config/constants.py`.
- Tareas en segundo plano / programadas: Celery + Celery Beat + Redis —
  decidido, aún no implementado (ningún módulo de negocio lo necesita
  todavía).

## Apps actuales

```
accounts/          Accesos y Seguridad — login Google, AllowedEmail,
                    RoleRequiredMixin / ApiKeyRequiredMixin (accounts/permissions.py)
feature_tracking/   Feature Tracking y Migraciones — registro de features,
                    estado, dependencias (webhooks.py recibe el merge a `prototype`)
integrations/       pool único de clientes a terceros — hoy solo integrations/github.py
config/             settings, urls raíz, constants.py (única fuente de env vars)
```

Antes de crear una app nueva, consultar la tabla de áreas vía
`obtener_lineamientos_backend` del MCP — no duplicar un área existente.

## Flujo de desarrollo

```bash
python -m venv venv
venv\Scripts\activate          # Windows
cp .env.example .env           # completar GOOGLE_CLIENT_ID
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser   # para /admin/
python manage.py runserver         # http://127.0.0.1:8000
```

Dar acceso a un correo nuevo: `/admin/` → Accounts → Allowed emails → Add.
Dar un rol: `/admin/` → Groups (crear el grupo) → asignar el `Group` al
`User` en `/admin/auth/user/`.

## Testing

No hay suite de tests todavía más allá de lo puntual en `accounts/tests.py`
y `feature_tracking/tests.py`. Todo `APIView` nuevo que toque un modelo o
integración externa lleva al menos un test (`python manage.py test`)
cubriendo el camino feliz y el rechazo por permisos.

## Variables de entorno

`.env` (ver `.env.example`): `SECRET_KEY`, `DEBUG`, `GOOGLE_CLIENT_ID`.
Cualquier variable nueva se agrega a `config/constants.py` (nunca se lee
`os.environ` directo en otro archivo) y a `.env.example` en el mismo
commit.
