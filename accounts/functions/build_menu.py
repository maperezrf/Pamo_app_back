from ..menu_config import MENU
from ..permissions import user_matches_roles


def build_menu_for_user(user):
    """Árbol de módulos/submódulos filtrado por los roles del usuario."""

    menu = []
    for area in MENU:
        if not user_matches_roles(user, area["roles"]):
            continue
        submodulos = [
            submodulo for submodulo in area["submodulos"]
            if user_matches_roles(user, submodulo["roles"])
        ]
        menu.append({**area, "submodulos": submodulos})
    return menu
