import hashlib
import hmac
import json

from django.test import TestCase, override_settings
from django.urls import reverse

from config.constants import GITHUB_WEBHOOK_SECRET, MCP_API_KEY

from .models import GovernancePrototipo


def firmar_payload(payload_bytes):
    digest = hmac.new(GITHUB_WEBHOOK_SECRET.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class GovernancePrototipoAPITests(TestCase):
    def setUp(self):
        self.list_url = reverse('feature_tracking:prototipo_list_create')
        self.datos_validos = {
            "nombre": "Prototipo de prueba",
            "descripcion": "Descripción de prueba",
            "estado": "activo",
            "ambiente": "desarrollo",
            "url_github": "https://github.com/org/repo",
            "creado_por": "tester@pamo.com",
        }

    def test_crear_sin_api_key_rechazado(self):
        response = self.client.post(self.list_url, self.datos_validos, content_type="application/json")
        self.assertEqual(response.status_code, 403)

    def test_crear_con_api_key_invalida_rechazado(self):
        response = self.client.post(
            self.list_url, self.datos_validos, content_type="application/json",
            HTTP_X_API_KEY="clave-incorrecta",
        )
        self.assertEqual(response.status_code, 403)

    def test_crear_con_api_key_valida(self):
        response = self.client.post(
            self.list_url, self.datos_validos, content_type="application/json",
            HTTP_X_API_KEY=MCP_API_KEY,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(GovernancePrototipo.objects.count(), 1)

    def test_no_se_puede_escribir_campos_de_merge_via_api(self):
        payload = dict(self.datos_validos, merged_to_produccion=True)
        response = self.client.post(
            self.list_url, payload, content_type="application/json",
            HTTP_X_API_KEY=MCP_API_KEY,
        )
        self.assertEqual(response.status_code, 201)
        self.assertFalse(GovernancePrototipo.objects.get().merged_to_produccion)

    def test_actualizar_parcial(self):
        prototipo = GovernancePrototipo.objects.create(**self.datos_validos)
        detail_url = reverse('feature_tracking:prototipo_detail', args=[prototipo.id])
        response = self.client.patch(
            detail_url, {"estado": "archivado"}, content_type="application/json",
            HTTP_X_API_KEY=MCP_API_KEY,
        )
        self.assertEqual(response.status_code, 200)
        prototipo.refresh_from_db()
        self.assertEqual(prototipo.estado, "archivado")


@override_settings(ALLOWED_HOSTS=["*"])
class GitHubMergeWebhookTests(TestCase):
    def setUp(self):
        self.webhook_url = reverse('feature_tracking:webhook_github_merge')
        self.prototipo = GovernancePrototipo.objects.create(
            nombre="Prototipo webhook",
            descripcion="Descripción",
            estado="activo",
            ambiente="desarrollo",
            url_github="https://github.com/org/repo",
            creado_por="tester@pamo.com",
        )

    def _payload(self, base_ref="dev", delivery="evento-1"):
        return json.dumps({
            "repository": {"html_url": "https://github.com/org/repo"},
            "pull_request": {
                "merged": True,
                "html_url": "https://github.com/org/repo/pull/1",
                "base": {"ref": base_ref},
            },
        }).encode(), delivery

    def test_firma_invalida_rechazada(self):
        body, _ = self._payload()
        response = self.client.post(
            self.webhook_url, data=body, content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256="sha256=invalida",
        )
        self.assertEqual(response.status_code, 401)

    def test_merge_a_dev_actualiza_registro(self):
        body, delivery = self._payload(base_ref="dev")
        response = self.client.post(
            self.webhook_url, data=body, content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=firmar_payload(body),
            HTTP_X_GITHUB_DELIVERY=delivery,
        )
        self.assertEqual(response.status_code, 200)
        self.prototipo.refresh_from_db()
        self.assertTrue(self.prototipo.merged_to_desarrollo)
        self.assertFalse(self.prototipo.merged_to_produccion)
        self.assertEqual(self.prototipo.github_event_id, delivery)

    def test_reentrega_del_mismo_evento_es_idempotente(self):
        body, delivery = self._payload(base_ref="main")
        firma = firmar_payload(body)
        for _ in range(2):
            response = self.client.post(
                self.webhook_url, data=body, content_type="application/json",
                HTTP_X_HUB_SIGNATURE_256=firma,
                HTTP_X_GITHUB_DELIVERY=delivery,
            )
            self.assertEqual(response.status_code, 200)

        self.prototipo.refresh_from_db()
        self.assertTrue(self.prototipo.merged_to_produccion)
