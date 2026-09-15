# App `accounts`

`accounts/` es dueña del acceso al backend: sesión, autorización por roles y
menú visible. No contiene reglas de negocio de otros módulos.

## Capacidades

- `AllowedEmail`: lista de correos que pueden entrar después de validar su
  identidad con Google. No sustituye los roles.
- Inicio de sesión con token de Google, sesión de Django y cierre de sesión.
- Autorización por grupos de Django mediante los mixins documentados en
  [`../patterns/AUTHORIZATION.md`](../patterns/AUTHORIZATION.md).
- Menú de navegación filtrado por los mismos roles de autorización.

## Rutas actuales

Las rutas públicas y autenticadas de acceso se publican bajo `/api/auth/`.
El detalle de método, permisos y respuesta está en
[`../contracts/API.md`](../contracts/API.md).

## Reglas de cambio

- Un correo permitido habilita la entrada; no concede automáticamente un
  rol de negocio.
- El criterio para roles es central: usar `user_matches_roles()` para que
  vistas y menú no diverjan.
- Un ítem del menú se agrega solo cuando hay una ruta y pantalla real en el
  frontend. Sus roles deben coincidir con los de la capacidad que expone.
- El código que consume una API interna máquina-a-máquina usa API key; un
  usuario humano usa sesión y roles.

## Pruebas

`accounts/tests.py` prueba el filtrado de menú para usuario sin grupos,
usuario con grupo y superusuario. Al cambiar permisos o navegación, ampliar
esta cobertura y añadir el rechazo explícito cuando aplique.
