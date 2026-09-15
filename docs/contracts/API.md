# Contrato HTTP publicado

Base actual: `/api/`. Las rutas de autenticación viven bajo `/api/auth/`.
Este documento describe solo el contrato implementado; no contiene secretos
ni datos de entorno.

| Método y ruta | Acceso | Entrada | Respuesta principal |
| --- | --- | --- | --- |
| `GET /api/auth/csrf/` | Público | — | `detail`, `csrftoken`. |
| `POST /api/auth/google/` | Público | `credential` de Google. | `authorized` y, si procede, `user`. |
| `GET /api/auth/me/` | Sesión | — | `email`, `username`. |
| `POST /api/auth/logout/` | Sesión | — | `detail: logged_out`. |
| `GET /api/auth/menu/` | Sesión | — | Árbol de áreas filtrado por roles. |
| `GET /api/auth/ping-admin/` | Rol `Admin` | — | `is_admin: true`. |

## Errores de acceso relevantes

- Sin sesión en rutas protegidas: `403` con la configuración actual de
  autenticación por sesión de DRF.
- Credencial Google ausente o inválida: `400` y `authorized: false`.
- Correo no verificado o no permitido: `403` y `authorized: false`.

Un cambio de esta tabla exige coordinar el consumidor del frontend antes de
considerarlo terminado.
