# Registro único del menú de navegación (módulos y submódulos). Cada área
# nueva agrega su entrada acá cuando tenga su primera pantalla y ruta de
# React Router listas -- ver docs/GOVERNANCE.md §3.2 (backend) y el
# equivalente §3 en el repo de frontend. `path` es la ruta de React Router
# que consume el `NavLink` del sidebar -- no se agrega un módulo/submódulo
# sin una pantalla real que enrutar. `roles` vacío = visible para cualquier
# usuario autenticado, mismo criterio que `allowed_roles` en
# RoleRequiredMixin.

MENU = [
    {
        "key": "inicio",
        "label": "Inicio",
        "path": "/",
        "roles": [],
        "submodulos": [],
    },
    {
        "key": "prototipos",
        "label": "Prototipos",
        "path": "/prototipos",
        "roles": ["Admin"],
        "submodulos": [],
    },
    {
        "key": "remisiones",
        "label": "Remisiones",
        "path": "/remisiones",
        "roles": ["Admin", "Operaciones", "Logistica"],
        "submodulos": [],
    },
    {
        "key": "facturacion-remisiones",
        "label": "Facturación de remisiones",
        "path": "/contabilidad/remisiones",
        "roles": ["Admin", "Facturacion"],
        "submodulos": [],
    },
]
