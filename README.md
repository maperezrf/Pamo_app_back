# Pamo App — Backend

Backend Django REST Framework de **Pamo** (marketplace). Organizado por
áreas de negocio (una app de Django por área — Accesos y Seguridad,
Productos, Logística, Facturación, etc.), con la lógica de negocio separada
en `functions/` y las conexiones a terceros (Shopify, Sodimac, Falabella,
Mercado Libre, Envía, Siigo...) centralizadas en `integrations/`.

Repo hermano: **frontend** en [`pamo_app_front`](https://github.com/maperezrf/pamo_app_front)
(React + Vite). Backend y frontend son repos separados que se despliegan de
forma independiente.

**Antes de escribir código, leer [`docs/GOVERNANCE.md`](docs/GOVERNANCE.md)**
— reglas de arquitectura, permisos, estructura de apps, integraciones y el
contrato con el frontend. Es de lectura obligatoria tanto para quien
programa a mano como para quien dirige el desarrollo con IA.

## Stack

- Python 3 + Django 5.2 + Django REST Framework 3.18
- Auth: Google OAuth (`google-auth`) + sesión de Django por cookie
- Base de datos: SQLite en local (Postgres pendiente de definir para
  producción)
- `django-cors-headers`, `python-decouple`

## 1. Credenciales de Google OAuth (una sola vez)

1. Entrar a [Google Cloud Console](https://console.cloud.google.com/) y
   crear (o seleccionar) un proyecto.
2. **APIs & Services → OAuth consent screen**. Tipo *External*, completar
   nombre de la app y correo de soporte.
3. **APIs & Services → Credentials → Create Credentials → OAuth client
   ID**. Tipo *Web application*.
4. En **Authorized JavaScript origins** agregar el origen del frontend
   (`http://localhost:5173` en local).
5. Copiar el **Client ID** generado (no hace falta el *Client secret*).
6. Pegarlo en `backend/.env` → `GOOGLE_CLIENT_ID` (y también en el `.env`
   del repo de frontend → `VITE_GOOGLE_CLIENT_ID`, es el mismo valor).

En producción hay que volver a este mismo client y agregar el dominio real
a "Authorized JavaScript origins".

## 2. Levantar el backend

```bash
python -m venv venv
venv\Scripts\activate          # Windows
cp .env.example .env           # completar GOOGLE_CLIENT_ID
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser   # para entrar a /admin/
python manage.py runserver
```

Queda en `http://127.0.0.1:8000`. Admin de Django en
`http://127.0.0.1:8000/admin/`.

## 3. Dar acceso a un correo nuevo

Con el backend corriendo, entrar a `http://127.0.0.1:8000/admin/` (con el
superusuario) → **Accounts → Allowed emails → Add**. Sin esa fila, aunque el
login con Google sea válido, el backend responde `403`.

## 4. Dar un rol a alguien

Los roles son `Group` estándar de Django, editables en `/admin/` →
**Groups**. Crear el grupo (ej. "Admin") y asignarlo al `User`
correspondiente en `/admin/auth/user/`.

Para restringir un endpoint nuevo a un rol, usar el mixin en
`accounts/permissions.py`:

```python
from accounts.permissions import RoleRequiredMixin

class MiEndpoint(RoleRequiredMixin, APIView):
    allowed_roles = ["Admin"]
```

Ver `docs/GOVERNANCE.md` §4 para el resto de reglas de vistas/permisos, §3
para en qué app va cada funcionalidad nueva, y §5 para cómo conectar un
proveedor externo.

## Notas

- Autenticación por **sesión de Django** (cookie), no JWT.
- Base de datos: SQLite en local. Cambiar a Postgres es editar
  `config/settings.py` → `DATABASES`.
- Si backend y frontend terminan en dominios distintos, revisar CORS y
  cookie cross-domain antes del primer deploy real — ver
  `docs/GOVERNANCE.md` §12.
