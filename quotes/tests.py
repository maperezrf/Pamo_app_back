from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core import signing
from django.test import SimpleTestCase, TestCase
from pypdf import PdfReader
from rest_framework.test import APIClient

from integrations.shopify.functions.download_image import DownloadImageError

from .functions import quote_links
from .functions.list_quotes import list_quotes
from .functions.quote_pdf import quote_pdf

GET_DRAFT = "quotes.apis.get_draft_order"
LIST_DRAFTS = "quotes.functions.list_quotes.list_draft_orders"
DOWNLOAD_IMAGE = "quotes.functions.quote_pdf.download_image"
PUBLIC_URL = "https://api.pamo.test"


def _draft(lines=1, **overrides):
    draft = {
        "id": "9", "name": "#D7318", "updated_at": "2026-10-22T03:25:07Z",
        "invoice_url": "https://www.pamo.co/1/invoices/abc", "discount": None, "total": "1190.00",
        "customer": {"display_name": "hm real estate", "email": "a@b.co", "phone": "+573001112233", "document": "[900382733]"},
        "line_items": [
            {"sku": f"SKU{n}", "title": f"Grifería {n}", "quantity": 1, "unit_price": "1190.00",
             "image_url": "https://cdn.shopify.com/p.png"}
            for n in range(lines)
        ],
    }
    return {**draft, **overrides}


def _pdf_text(content):
    reader = PdfReader(BytesIO(content))
    return len(reader.pages), "\n".join(page.extract_text() for page in reader.pages)


@patch(DOWNLOAD_IMAGE, side_effect=DownloadImageError("sin red"))
class QuotePdfTests(SimpleTestCase):
    def test_cover_pages_customer_and_totals(self, _download):
        filename, content = quote_pdf(_draft())

        self.assertEqual(filename, "cotizacion_D7318.pdf")
        pages, text = _pdf_text(content)
        self.assertEqual(pages, 3)  # portada, ¿por qué Pamo? y productos
        # 03:25 UTC del 22 son las 22:25 del 21 en Colombia: vence el 21 + 10.
        self.assertIn("Fecha de Vencimiento: 31/10/2026", text)
        self.assertIn("Hm Real Estate", text)
        self.assertIn("900382733", text)
        self.assertIn("3001112233", text)
        self.assertIn("$ 1.000", text)  # subtotal sin IVA
        self.assertNotIn("Descuento", text)

    def test_nit_is_read_from_the_document_metafield(self, _download):
        draft = _draft()
        draft["customer"] = {**draft["customer"], "document": "sin corchetes"}

        _, text = _pdf_text(quote_pdf(draft)[1])

        self.assertNotIn("Nit:", text)

    def test_seven_products_per_page(self, _download):
        pages, text = _pdf_text(quote_pdf(_draft(lines=15, total="17850.00"))[1])

        self.assertGreaterEqual(pages, 5)  # 15 productos: 3 hojas de productos
        self.assertIn("SKU14", text)

    def test_fixed_discount_shows_its_percentage_and_the_shopify_amount(self, _download):
        # Como D7238: $106.904 fijo sobre $1.527.200 de lista = 7%.
        discount = {"title": "PAMO EMPRESA · 7 % efectivo", "value": 106904.0, "value_type": "FIXED_AMOUNT", "amount": "106904.0"}
        draft = _draft(discount=discount, total="1420296.00")
        draft["line_items"][0].update(quantity=8, unit_price="190900.00")

        _, text = _pdf_text(quote_pdf(draft)[1])

        self.assertIn("Descuento (7%)", text)
        self.assertIn("$ 106.904", text)  # el monto de Shopify, con IVA
        self.assertNotIn("PAMO EMPRESA", text)

    def test_percentage_discount_uses_the_shopify_value(self, _download):
        discount = {"title": "", "value": 5.0, "value_type": "PERCENTAGE", "amount": "59.50"}

        _, text = _pdf_text(quote_pdf(_draft(discount=discount, total="1130.50"))[1])

        self.assertIn("Descuento (5%)", text)

    def test_a_failed_photo_does_not_break_the_pdf(self, download):
        _, content = quote_pdf(_draft(lines=2))

        self.assertTrue(content.startswith(b"%PDF"))
        self.assertEqual(download.call_count, 2)


class QuoteLinkTests(SimpleTestCase):
    def test_round_trip(self):
        with patch.object(quote_links, "BACKEND_PUBLIC_URL", PUBLIC_URL):
            link = quote_links.quote_link("9")

        self.assertTrue(link.startswith(f"{PUBLIC_URL}/api/quotes/pdf/"))
        self.assertEqual(quote_links.quote_id_from_link(link.rstrip("/").rsplit("/", 1)[-1]), "9")

    def test_no_public_url_means_no_link(self):
        with patch.object(quote_links, "BACKEND_PUBLIC_URL", ""):
            self.assertEqual(quote_links.quote_link("9"), "")


class QuoteListTests(SimpleTestCase):
    @patch(LIST_DRAFTS)
    def test_rows_like_pamo_web(self, list_drafts):
        list_drafts.return_value = [{
            "id": "9", "name": "#D7318", "created_at": "2026-10-22T03:25:07Z", "tags": ["andrea", "x"],
            "total": "945367.82", "customer": {"first_name": "HM", "last_name": "real estate", "company": "900382733-3 FEPRIN"},
        }]

        with patch.object(quote_links, "BACKEND_PUBLIC_URL", PUBLIC_URL):
            rows = list_quotes(" D7318 ")

        list_drafts.assert_called_once_with(first=250, query="D7318")
        row = rows[0]
        self.assertEqual(
            {key: row[key] for key in ("id", "name", "created_at", "customer", "total", "seller", "nit")},
            {"id": "9", "name": "#D7318", "created_at": "2026-10-21", "customer": "Hm Real Estate",
             "total": "945367.82", "seller": "andrea", "nit": "900382733"},
        )
        self.assertTrue(row["pdf_link"].startswith(PUBLIC_URL))


@patch(DOWNLOAD_IMAGE, side_effect=DownloadImageError("sin red"))
class QuoteApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user("ops", password="x")
        self.user.groups.add(Group.objects.create(name="Operaciones"))

    @patch(LIST_DRAFTS, return_value=[])
    def test_list_requires_the_role(self, _list, _download):
        self.assertEqual(self.client.get("/api/quotes/").status_code, 403)
        self.client.force_authenticate(User.objects.create_user("otro", password="x"))
        self.assertEqual(self.client.get("/api/quotes/").status_code, 403)

        self.client.force_authenticate(self.user)
        response = self.client.get("/api/quotes/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"quotes": []})

    @patch(GET_DRAFT, return_value=_draft())
    def test_pdf_download_with_the_role(self, get_draft, _download):
        self.assertEqual(self.client.get("/api/quotes/9/pdf/").status_code, 403)

        self.client.force_authenticate(self.user)
        response = self.client.get("/api/quotes/9/pdf/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertEqual(response["Content-Disposition"], 'attachment; filename="cotizacion_D7318.pdf"')
        get_draft.assert_called_with(9)

    @patch(GET_DRAFT, return_value=None)
    def test_pdf_of_a_missing_quote_is_404(self, _get, _download):
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get("/api/quotes/9/pdf/").status_code, 404)

    @patch(GET_DRAFT, return_value=_draft())
    def test_public_link_opens_without_session(self, get_draft, _download):
        with patch.object(quote_links, "BACKEND_PUBLIC_URL", PUBLIC_URL):
            link = quote_links.quote_link("9")

        response = self.client.get(link.removeprefix(PUBLIC_URL))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertEqual(response["Content-Disposition"], 'inline; filename="cotizacion_D7318.pdf"')
        get_draft.assert_called_once_with("9")

    @patch(GET_DRAFT, return_value=_draft())
    def test_tampered_link_is_404(self, get_draft, _download):
        forged = signing.dumps({"q": "9"}, salt="otra-cosa")

        self.assertEqual(self.client.get(f"/api/quotes/pdf/{forged}/").status_code, 404)
        self.assertEqual(self.client.get("/api/quotes/pdf/9/").status_code, 404)
        get_draft.assert_not_called()

    @patch(GET_DRAFT, return_value=_draft())
    def test_expired_link_is_410(self, get_draft, _download):
        token = signing.dumps({"q": "9"}, salt="quotes.pdf-link")

        with patch.object(quote_links, "LINK_MAX_AGE", timedelta(seconds=-1)):
            response = self.client.get(f"/api/quotes/pdf/{token}/")

        self.assertEqual(response.status_code, 410)
        get_draft.assert_not_called()

    @patch(GET_DRAFT, return_value=None)
    def test_link_to_a_deleted_quote_is_404(self, _get, _download):
        token = signing.dumps({"q": "9"}, salt="quotes.pdf-link")
        self.assertEqual(self.client.get(f"/api/quotes/pdf/{token}/").status_code, 404)
