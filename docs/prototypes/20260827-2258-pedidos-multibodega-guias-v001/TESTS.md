# TESTS — 20260827-2258-pedidos-multibodega-guias-v001

| Estado | Repositorio/perfil | Prueba | Ambiente | Evidencia |
|---|---|---|---|---|
| PASS | Backend / shared + áreas integradas | `python manage.py check` | entorno Python temporal aislado | System check sin errores |
| PASS | Backend / full suite | `python manage.py test` | SQLite temporal, integraciones externas desactivadas | 256 pruebas aprobadas en 11,56 s |
| PASS | Frontend / lint | `npm run lint` | dependencias reproducidas con `npm ci` | 0 errores; 3 advertencias heredadas de Catálogo |
| PASS | Frontend / build | `npm run build` | Vite local | compilación aprobada; advertencia no bloqueante de tamaño de chunk |
| PASS | Pedidos / QA visual original | escritorio y móvil | servicios locales aislados | flujo operativo visible y sin overflow horizontal |
| PASS | Continuidad | comparación final contra `origin/dev` | worktrees de handoff | 0 archivos eliminados de `dev`; Remisiones y Facturación preservados |

## Fallos, bloqueos y pruebas no ejecutadas

- No se ejecutó un despliegue ni QA sobre Beta o Producción.
- No se aplicaron migraciones en una base compartida.
- No se probaron envíos externos, compras de guía ni contactos reales.
- El escáner determinista devolvió `BLOCKED_SECRET` por expresiones dinámicas. La revisión humana autorizada confirmó falsos positivos; la excepción se limita a este expediente y no desactiva el control.
