# Rol: desarrollador

El desarrollador ejecuta un plan vigente y preserva el conocimiento del
repositorio para la siguiente tarea.

## Proceso

1. Leer `docs/INDEX.md`, el plan y los documentos que este indique antes de
   abrir código.
2. Verificar en el archivo puntual que el plan sigue siendo válido.
3. Reutilizar las capacidades documentadas y mantener los límites de apps.
4. Implementar y ejecutar las pruebas proporcionales al riesgo.
5. Antes de cerrar, revisar si cambió una capacidad reutilizable, contrato,
   regla no obvia o integración; actualizar su documentación cuando aplique.

Un cambio local, obvio y sin reutilización razonable no requiere crear un
archivo Markdown. No se documentan secretos, tokens ni valores de entorno.
No se guarda conocimiento de proyecto únicamente en memoria privada del
agente: lo reutilizable o necesario para continuidad se registra en `docs/`.
