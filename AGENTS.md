# Instrucciones para agentes — Backend Pamo

## Inicio obligatorio

Antes de planear, leer código o modificar archivos:

1. Leer [`docs/INDEX.md`](docs/INDEX.md) completo.
2. Leer los documentos de arquitectura, app, patrón y contrato aplicables.
3. Buscar una capacidad reutilizable ya documentada.

El código solo se inspecciona después para comprobar el estado real,
localizar una implementación o responder una duda que la documentación no
cubra. No explorar archivos indiscriminadamente como sustituto de contexto.

## Contexto compartido

`docs/` es la única fuente durable y compartida de conocimiento del proyecto.
No depender de memoria privada del agente, historial de chat, resúmenes de
IDE ni archivos fuera del repositorio. Si una memoria externa revela un dato
útil, verificarlo contra documentación y código; si sigue siendo relevante,
registrarlo en el documento versionado correspondiente.

## Ejecución y cierre

- Reutilizar antes de crear mixins, helpers, clientes, contratos o servicios.
- Mantener secretos en variables de entorno y `config/constants.py`; nunca
  documentar valores sensibles.
- Probar en proporción al cambio.
- Antes de cerrar, actualizar el patrón, expediente de app o contrato si se
  creó, modificó o deprecó una capacidad reutilizable o una interfaz externa.
- Los cambios locales y evidentes no requieren un documento nuevo.

Los roles detallados están en [`docs/roles/`](docs/roles/).
