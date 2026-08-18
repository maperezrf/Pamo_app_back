from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

User = get_user_model()

TEST_MENU = [
    {"key": "inicio", "label": "Inicio", "path": "/", "roles": [], "submodulos": []},
    {
        "key": "logistica",
        "label": "Logistica",
        "path": "/logistica",
        "roles": ["Logistica"],
        "submodulos": [
            {"key": "seguimiento", "label": "Seguimiento de pedidos", "path": "/logistica/seguimiento", "roles": []},
            {"key": "guias", "label": "Guias", "path": "/logistica/guias", "roles": ["Admin"]},
        ],
    },
]


class MenuAPITests(TestCase):
    def setUp(self):
        self.logistica_group, _ = Group.objects.get_or_create(name="Logistica")
        Group.objects.get_or_create(name="Admin")

    def test_anonimo_no_autorizado(self):
        response = self.client.get("/api/auth/menu/")
        self.assertEqual(response.status_code, 403)

    @patch("accounts.functions.build_menu.MENU", TEST_MENU)
    def test_usuario_sin_grupos_solo_ve_modulos_publicos(self):
        user = User.objects.create_user(username="sin-grupo@pamo.test")
        self.client.force_login(user)

        response = self.client.get("/api/auth/menu/")

        self.assertEqual(response.status_code, 200)
        keys = [area["key"] for area in response.json()]
        self.assertEqual(keys, ["inicio"])

    @patch("accounts.functions.build_menu.MENU", TEST_MENU)
    def test_usuario_con_grupo_ve_su_area_y_submodulos_filtrados(self):
        user = User.objects.create_user(username="logistica@pamo.test")
        user.groups.add(self.logistica_group)
        self.client.force_login(user)

        response = self.client.get("/api/auth/menu/")

        data = response.json()
        logistica = next(area for area in data if area["key"] == "logistica")
        submodulo_keys = [sm["key"] for sm in logistica["submodulos"]]
        self.assertEqual(submodulo_keys, ["seguimiento"])

    @patch("accounts.functions.build_menu.MENU", TEST_MENU)
    def test_superusuario_ve_todo(self):
        user = User.objects.create_superuser(username="admin@pamo.test", email="admin@pamo.test")
        self.client.force_login(user)

        response = self.client.get("/api/auth/menu/")

        data = response.json()
        logistica = next(area for area in data if area["key"] == "logistica")
        submodulo_keys = [sm["key"] for sm in logistica["submodulos"]]
        self.assertCountEqual(submodulo_keys, ["seguimiento", "guias"])
