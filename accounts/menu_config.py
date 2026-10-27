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
        "key": "orders",
        "label": "Pedidos",
        "path": "/orders",
        # Mismos roles que `orders.apis.ORDERS_LIST_ROLES` (GET /api/orders/).
        "roles": ["Admin", "Operaciones"],
        "submodulos": [],
    },
    {
        "key": "invoicing",
        "label": "Facturación",
        "path": "/invoicing",
        # Mismos roles que `invoicing.apis.INVOICES_LIST_ROLES` (GET /api/invoicing/).
        "roles": ["Admin", "Operaciones"],
        "submodulos": [],
    },
    {
        "key": "quotes",
        "label": "Cotizaciones",
        "path": "/quotes",
        # Mismos roles que `quotes.apis.QUOTES_ROLES` (GET /api/quotes/).
        "roles": ["Admin", "Operaciones"],
        "submodulos": [],
    },
    {
        "key": "products",
        "label": "Productos",
        "path": "/products",
        # Mismos roles que `products.apis.CATALOG_ROLES` (GET /api/products/).
        "roles": ["Admin"],
        "submodulos": [],
    },
    {
        "key": "processes",
        "label": "Procesos",
        "path": "/processes",
        # Mismos roles que `orchestrator.apis.ALLOWED_ROLES`.
        "roles": ["Admin", "Operaciones"],
        "submodulos": [],
    },
]
