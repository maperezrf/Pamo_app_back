# Patrón: autorización

Ubicación: `accounts/permissions.py`.

La autenticación humana se realiza con sesión de Django. La autorización se
declara de forma explícita en cada `APIView`, según quién consume el
endpoint.

## Elegir el mecanismo

| Consumidor | Usar | Resultado |
| --- | --- | --- |
| Usuario autenticado, sin rol particular | `IsAuthenticated` | Requiere sesión. |
| Usuario con uno o más grupos | `RoleRequiredMixin` | Requiere sesión y alguno de los grupos indicados. |
| Proceso interno sin sesión | `ApiKeyRequiredMixin` | Requiere `X-API-Key` válido. |
| Lectura para humano con rol o proceso interno | `RoleOrApiKeyRequiredMixin` | Acepta uno de los dos mecanismos. |
| Sistema externo propio con token dedicado (ej. RPA) | `BearerTokenRequiredMixin` | Requiere `Authorization: Bearer` (o `X-Bot-Token`) igual a `bearer_token` de la vista. |

## Uso con roles

```python
from rest_framework.views import APIView
from accounts.permissions import RoleRequiredMixin


class ReportAPI(RoleRequiredMixin, APIView):
    allowed_roles = ["Admin", "Operaciones"]
```

Una lista vacía permite a cualquier usuario autenticado. Un superusuario
siempre pasa el control. Los grupos se comparan por nombre mediante
`user_matches_roles()`; usar esta misma función para decisiones de
navegación o visibilidad relacionadas con roles.

## API key interna

`ApiKeyRequiredMixin` es únicamente para consumidor máquina-a-máquina
interno. No reemplaza permisos de usuario, no se usa para navegador ni para
webhooks de proveedores. La clave se compara en tiempo constante y su valor
nunca se incluye en documentación, logs o respuestas.

## Token dedicado por consumidor

`BearerTokenRequiredMixin` es para un sistema externo propio que no es un
usuario ni el servidor MCP, como el RPA de guías de Sodimac. Cada vista
declara su propio secreto (`bearer_token = CONSTANTE` de
`config/constants.py`), así cada consumidor tiene un token distinto y
revocable sin afectar a los demás. El token se acepta en
`Authorization: Bearer <token>` o en `X-Bot-Token: <token>` (así lo manda
el RPA heredado de `pamo_web`). Sin token configurado nadie pasa. La vista
no usa sesión ni CSRF.

## Verificación

Todo endpoint sensible debe probar al menos: consumidor autorizado y usuario
sin la sesión, rol o clave requeridos. Un rechazo es `403`, no un error 500.
