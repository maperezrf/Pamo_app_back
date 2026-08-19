# Governance de Pamo App — Backend

> Este documento es un **puntero corto**. El detalle completo de
> arquitectura, convenciones y checklist de este repo (`Pamo_app_back`,
> Django + DRF) vive en el servidor MCP `pamo-server` — consultarlo ahí
> para evitar que dos copias del mismo contenido se desincronicen.

## Dónde está cada cosa ahora

Todo lo que antes vivía acá (principios generales, tabla de áreas de
negocio, vistas y permisos, pool de integraciones, configuración y
secretos, modelos/migraciones/Celery, contrato con el frontend, ciclo de
vida de un feature, testing, checklist) se consulta con las herramientas
del MCP `pamo-server`:

| Herramienta MCP | Qué cubre |
|---|---|
| `obtener_mapa_documentacion` | Índice de toda la documentación — consultar primero |
| `obtener_lineamientos_generales` | Principios que aplican a todo el proyecto, sin importar el área |
| `obtener_lineamientos_backend` | Apps por área, vistas/permisos, integraciones, secretos, modelos/Celery, contrato con el frontend, ciclo prototype → QA → producción, testing, checklist |
| `obtener_lineamientos_git` | Flujo de ramas y Pull Requests, regla dura de no tocar `main`/producción |

El repo de frontend (`pamo_app_front`) es un proyecto independiente con su
propio `docs/GOVERNANCE.md`, igual de corto, apuntando al mismo MCP.

## Cómo evoluciona esto

Backend, frontend y el servidor MCP (`pamo-server`) son tres repositorios
independientes. Una decisión de arquitectura nueva, o un patrón que deja de
servir, se edita directo en los documentos que sirve el MCP
(`docs/lineamientos-*.md` de ese repo), dejando registro de qué cambió y
por qué — no en este archivo, que ya no acumula contenido nuevo.
