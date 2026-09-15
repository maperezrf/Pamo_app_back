# CLAUDE.md — Backend Pamo

Guía para Claude Code y otros agentes en este repositorio Django + DRF.

## Contexto obligatorio

Antes de proponer un plan, leer código o realizar un cambio, leer
[`docs/INDEX.md`](docs/INDEX.md) completo. Después, leer la arquitectura,
el expediente de la app, patrón y contrato que correspondan a la tarea.

El código se consulta únicamente después de ese contexto para validar una
afirmación, localizar una implementación o resolver un vacío puntual. Buscar
primero una capacidad reutilizable documentada; no duplicar mixins, helpers,
clientes ni contratos existentes.

Si código y documentación discrepan, comprobar el comportamiento real y
actualizar el documento afectado en el mismo cambio cuando corresponda.

La memoria privada de Claude, los resúmenes del IDE y el historial de chat no
son fuente de verdad ni sustituyen `docs/`. Todo conocimiento necesario para
otro agente debe quedar documentado y versionado dentro de este repositorio.

## Reglas técnicas estables

- Django 5.2 + Django REST Framework; sesión por cookie, no JWT.
- La configuración se lee mediante `config/constants.py`; no usar
  `os.environ` directamente ni poner secretos en código o documentación.
- La lógica de negocio vive en `functions/`, no en vistas HTTP.
- Los proveedores externos viven en `integrations/<provider>/`; el cliente
  es transporte genérico y las operaciones de negocio viven en `functions/`.
- Todo `APIView` nuevo que toque modelos o proveedores lleva pruebas de
  camino feliz y rechazo por permisos. Ejecutar `python manage.py check`.
- Antes de crear una app, revisar
  [`docs/architecture/APP_BOUNDARIES.md`](docs/architecture/APP_BOUNDARIES.md).

Para reglas operativas compartidas, ver `AGENTS.md`.
