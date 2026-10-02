from decimal import Decimal
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from orders.models import MarketplaceOrder

from .functions.expand_product import EmptyKitError, expand_product
from .functions.export_equivalences import export_equivalences
from .functions.export_kits import export_kits
from .functions.import_equivalences import InvalidColumnsError, import_equivalences
from .functions.import_kits import import_kits
from .functions.resolve_marketplace_sku import resolve_marketplace_sku
from .models import KitComponent, Marketplace, MarketplaceSku, Product, SkuUpload


def _kit(sku, *components):
    kit = Product.objects.create(sku=sku, is_kit=True)
    for component_sku, quantity in components:
        component, _ = Product.objects.get_or_create(sku=component_sku)
        KitComponent.objects.create(kit=kit, component=component, quantity=quantity)
    return kit


class MarketplaceAliasTests(TestCase):
    def test_orders_marketplace_is_the_products_enum(self):
        self.assertIs(MarketplaceOrder.Marketplace, Marketplace)
        self.assertEqual(MarketplaceOrder.Marketplace.SODIMAC, "sodimac")


class ResolveMarketplaceSkuTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(sku="pamo123")
        MarketplaceSku.objects.create(product=self.product, marketplace=Marketplace.SODIMAC, sku="54654")

    def test_finds_the_product(self):
        self.assertEqual(resolve_marketplace_sku(Marketplace.SODIMAC, "54654"), self.product)

    def test_strips_spaces(self):
        self.assertEqual(resolve_marketplace_sku(Marketplace.SODIMAC, " 54654 "), self.product)

    def test_returns_none_when_missing_or_other_marketplace(self):
        self.assertIsNone(resolve_marketplace_sku(Marketplace.SODIMAC, "999"))
        self.assertIsNone(resolve_marketplace_sku(Marketplace.MADECENTRO, "54654"))
        self.assertIsNone(resolve_marketplace_sku(Marketplace.SODIMAC, ""))


class ExpandProductTests(TestCase):
    def test_simple_product_is_itself(self):
        product = Product.objects.create(sku="pamo123")
        self.assertEqual(expand_product(product, 3), [{"sku": "pamo123", "quantity": 3}])

    def test_kit_expands_to_components_multiplying_quantities(self):
        kit = _kit("5864", ("A", 1), ("B", 2))
        self.assertEqual(
            expand_product(kit, 2),
            [{"sku": "A", "quantity": 2}, {"sku": "B", "quantity": 4}],
        )

    def test_empty_kit_raises(self):
        kit = Product.objects.create(sku="5864", is_kit=True)
        with self.assertRaises(EmptyKitError):
            expand_product(kit, 1)

    def test_simple_product_keeps_its_price(self):
        product = Product.objects.create(sku="pamo123")
        self.assertEqual(
            expand_product(product, 3, "100.005"), [{"sku": "pamo123", "quantity": 3, "price": Decimal("100.01")}]
        )

    def test_kit_price_is_split_equally_per_component_unit(self):
        # 3 unidades de componente por kit (1 A + 2 B): 300 / 3 = 100 c/u.
        kit = _kit("5864", ("A", 1), ("B", 2))
        lines = expand_product(kit, 2, "300")
        self.assertEqual(
            lines,
            [
                {"sku": "A", "quantity": 2, "price": Decimal("100.00")},
                {"sku": "B", "quantity": 4, "price": Decimal("100.00")},
            ],
        )
        # El total se conserva: 2 kits × 300.
        self.assertEqual(sum(line["price"] * line["quantity"] for line in lines), Decimal("600"))

    def test_kit_price_split_rounds_each_line_to_cents(self):
        kit = _kit("5864", ("A", 1), ("B", 2))
        self.assertEqual([line["price"] for line in expand_product(kit, 1, "100")], [Decimal("33.33")] * 2)


class ImportEquivalencesTests(TestCase):
    def test_creates_products_and_equivalences(self):
        result = import_equivalences([
            {"pamo_sku": "pamo123", "sodimac_sku": "54654", "sodimac_ean": "7701", "madecentro_sku": "PM789"},
        ])
        self.assertEqual(result["created"], 2)
        self.assertEqual(result["products_created"], 1)
        self.assertEqual(result["errors"], [])
        product = resolve_marketplace_sku(Marketplace.SODIMAC, "54654")
        self.assertEqual(product.sku, "pamo123")
        self.assertEqual(product.marketplace_skus.get(marketplace="sodimac").ean, "7701")
        self.assertEqual(resolve_marketplace_sku(Marketplace.MADECENTRO, "PM789"), product)

    def test_moves_a_sku_to_another_product_and_reports_it(self):
        import_equivalences([{"pamo_sku": "pamo1", "sodimac_sku": "54654"}])
        result = import_equivalences([{"pamo_sku": "pamo2", "sodimac_sku": "54654"}])
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["moved"], [{"marketplace": "sodimac", "sku": "54654", "from": "pamo1", "to": "pamo2"}])
        self.assertEqual(resolve_marketplace_sku(Marketplace.SODIMAC, "54654").sku, "pamo2")

    def test_several_skus_of_the_same_marketplace_via_repeated_rows(self):
        import_equivalences([
            {"pamo_sku": "pamo123", "sodimac_sku": "54654"},
            {"pamo_sku": "pamo123", "sodimac_sku": "54655"},
        ])
        self.assertEqual(Product.objects.get(sku="pamo123").marketplace_skus.count(), 2)

    def test_missing_ean_column_keeps_it_but_empty_ean_clears_it(self):
        import_equivalences([{"pamo_sku": "p", "sodimac_sku": "1", "sodimac_ean": "7701"}])
        import_equivalences([{"pamo_sku": "p", "sodimac_sku": "1"}])
        self.assertEqual(MarketplaceSku.objects.get(sku="1").ean, "7701")
        import_equivalences([{"pamo_sku": "p", "sodimac_sku": "1", "sodimac_ean": ""}])
        self.assertEqual(MarketplaceSku.objects.get(sku="1").ean, "")

    def test_normalizes_numbers_read_from_excel(self):
        import_equivalences([{"pamo_sku": "p", "sodimac_sku": 54654.0, "sodimac_ean": "7701234567890.0"}])
        equivalence = MarketplaceSku.objects.get()
        self.assertEqual((equivalence.sku, equivalence.ean), ("54654", "7701234567890"))

    def test_unknown_column_rejects_everything(self):
        with self.assertRaises(InvalidColumnsError) as context:
            import_equivalences([{"pamo_sku": "p", "sodimak_sku": "1"}])
        self.assertEqual(context.exception.columns, ["sodimak_sku"])
        self.assertFalse(Product.objects.exists())

    def test_row_errors_do_not_abort_the_load(self):
        result = import_equivalences([
            {"pamo_sku": "", "sodimac_sku": "1"},
            {"pamo_sku": "p", "sodimac_ean": "7701"},
            {"pamo_sku": "ok", "sodimac_sku": "2"},
        ])
        self.assertEqual([error["row"] for error in result["errors"]], [1, 2])
        self.assertEqual(result["created"], 1)

    def test_same_marketplace_sku_for_two_products_in_one_load_is_rejected(self):
        result = import_equivalences([
            {"pamo_sku": "p1", "sodimac_sku": "1"},
            {"pamo_sku": "p2", "sodimac_sku": "1"},
        ])
        self.assertEqual([error["row"] for error in result["errors"]], [2])
        self.assertEqual(resolve_marketplace_sku(Marketplace.SODIMAC, "1").sku, "p1")

    def test_round_trip_changes_nothing(self):
        import_equivalences([
            {"pamo_sku": "p1", "sodimac_sku": "1", "sodimac_ean": "77", "madecentro_sku": "M1"},
            {"pamo_sku": "p1", "sodimac_sku": "2"},
            {"pamo_sku": "p2"},
        ])
        exported = export_equivalences()
        result = import_equivalences(exported["rows"])
        self.assertEqual((result["created"], result["updated"], result["moved"]), (0, 0, []))
        self.assertEqual(export_equivalences(), exported)


class ExportEquivalencesTests(TestCase):
    def test_one_row_per_product_repeating_for_extra_skus(self):
        import_equivalences([
            {"pamo_sku": "p1", "sodimac_sku": "1", "madecentro_sku": "M1"},
            {"pamo_sku": "p1", "sodimac_sku": "2"},
        ])
        Product.objects.create(sku="p2")
        data = export_equivalences()
        self.assertIn("sodimac_ean", data["columns"])
        rows = [(row["pamo_sku"], row["sodimac_sku"], row["madecentro_sku"]) for row in data["rows"]]
        self.assertEqual(rows, [("p1", "1", "M1"), ("p1", "2", ""), ("p2", "", "")])


class ImportKitsTests(TestCase):
    def test_creates_kit_and_missing_products(self):
        result = import_kits([
            {"kit_sku": "5864", "component_sku": "A", "quantity": 1},
            {"kit_sku": "5864", "component_sku": "B", "quantity": "2"},
        ])
        self.assertEqual((result["created"], result["errors"]), (1, []))
        kit = Product.objects.get(sku="5864")
        self.assertTrue(kit.is_kit)
        self.assertEqual(expand_product(kit, 1), [{"sku": "A", "quantity": 1}, {"sku": "B", "quantity": 2}])

    def test_replaces_the_whole_component_set(self):
        _kit("5864", ("A", 1), ("B", 2))
        result = import_kits([{"kit_sku": "5864", "component_sku": "A", "quantity": 3}])
        self.assertEqual(result["updated"], 1)
        self.assertEqual(expand_product(Product.objects.get(sku="5864"), 1), [{"sku": "A", "quantity": 3}])

    def test_kits_not_in_the_load_are_untouched(self):
        _kit("OTHER", ("A", 1))
        import_kits([{"kit_sku": "5864", "component_sku": "B", "quantity": 1}])
        self.assertEqual(Product.objects.get(sku="OTHER").components.count(), 1)

    def test_one_invalid_row_leaves_the_whole_kit_untouched(self):
        _kit("5864", ("A", 1))
        result = import_kits([
            {"kit_sku": "5864", "component_sku": "B", "quantity": 1},
            {"kit_sku": "5864", "component_sku": "C", "quantity": 0},
        ])
        self.assertEqual([error["row"] for error in result["errors"]], [2])
        self.assertEqual(expand_product(Product.objects.get(sku="5864"), 1), [{"sku": "A", "quantity": 1}])

    def test_rejects_nested_kits(self):
        _kit("K1", ("A", 1))
        result = import_kits([
            {"kit_sku": "K2", "component_sku": "K1", "quantity": 1},  # componente que es kit
            {"kit_sku": "A", "component_sku": "B", "quantity": 1},    # kit que es componente
            {"kit_sku": "K3", "component_sku": "K4", "quantity": 1},  # componente que es kit en la misma carga
            {"kit_sku": "K4", "component_sku": "C", "quantity": 1},
        ])
        self.assertEqual([error["row"] for error in result["errors"]], [1, 2, 3])
        self.assertFalse(Product.objects.filter(sku="K2").exists())
        self.assertFalse(Product.objects.get(sku="A").is_kit)
        self.assertTrue(Product.objects.get(sku="K4").is_kit)

    def test_rejects_self_reference_and_repeated_component(self):
        result = import_kits([
            {"kit_sku": "K1", "component_sku": "K1", "quantity": 1},
            {"kit_sku": "K2", "component_sku": "A", "quantity": 1},
            {"kit_sku": "K2", "component_sku": "A", "quantity": 2},
        ])
        self.assertEqual([error["row"] for error in result["errors"]], [1, 3])
        self.assertFalse(Product.objects.filter(is_kit=True).exists())

    def test_round_trip_changes_nothing(self):
        _kit("K1", ("A", 1), ("B", 2))
        _kit("K2", ("A", 3))
        exported = export_kits()
        result = import_kits(exported["rows"])
        self.assertEqual((result["created"], result["updated"], result["errors"]), (0, 0, []))
        self.assertEqual(export_kits(), exported)


class CatalogAPITests(APITestCase):
    def setUp(self):
        admin_group, _ = Group.objects.get_or_create(name="Admin")
        self.admin = User.objects.create_user("admin")
        self.admin.groups.add(admin_group)
        self.other = User.objects.create_user("other")
        _kit("5864", ("A", 1))
        import_equivalences([{"pamo_sku": "5864", "sodimac_sku": "5864", "sodimac_ean": "77"}])

    def _endpoints(self):
        return [
            ("get", reverse("products-list"), None),
            ("get", reverse("products-equivalences"), None),
            ("post", reverse("products-equivalences"), {"rows": [{"pamo_sku": "p"}]}),
            ("get", reverse("products-kits"), None),
            ("post", reverse("products-kits"), {"rows": [{"kit_sku": "K", "component_sku": "A", "quantity": 1}]}),
        ]

    def test_rejects_anonymous_and_users_without_role(self):
        for method, url, body in self._endpoints():
            with self.subTest(method=method, url=url):
                self.assertEqual(getattr(self.client, method)(url, body, format="json").status_code, 403)
        self.client.force_login(self.other)
        for method, url, body in self._endpoints():
            with self.subTest(method=method, url=url, user="other"):
                self.assertEqual(getattr(self.client, method)(url, body, format="json").status_code, 403)

    def test_product_list_with_search(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("products-list"), {"search": "5864"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        product = response.data["results"][0]
        self.assertEqual(product["sku"], "5864")
        self.assertEqual(product["components"], [{"sku": "A", "quantity": 1}])
        self.assertEqual(product["marketplace_skus"], [{"marketplace": "sodimac", "sku": "5864", "ean": "77"}])

    def test_equivalences_download_and_upload(self):
        self.client.force_login(self.admin)
        download = self.client.get(reverse("products-equivalences"))
        self.assertEqual(download.status_code, 200)
        expected = {"pamo_sku": "5864", "sodimac_sku": "5864", "sodimac_ean": "77"}
        self.assertLessEqual(expected.items(), download.data["rows"][0].items())

        upload = self.client.post(
            reverse("products-equivalences"),
            {"rows": [{"pamo_sku": "pamo123", "sodimac_sku": "54654"}]},
            format="json",
        )
        self.assertEqual(upload.status_code, 200)
        self.assertEqual(upload.data["created"], 1)

    def test_upload_with_unknown_column_is_400(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("products-equivalences"), {"rows": [{"pamo_sku": "p", "sku_sodimac": "1"}]}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["unknown_columns"], ["sku_sodimac"])

    def test_upload_without_rows_is_400(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("products-kits"), {"rows": "nope"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_kits_download_and_upload(self):
        self.client.force_login(self.admin)
        download = self.client.get(reverse("products-kits"))
        self.assertEqual(download.data["rows"], [{"kit_sku": "5864", "component_sku": "A", "quantity": 1}])
        upload = self.client.post(
            reverse("products-kits"),
            {"rows": [{"kit_sku": "5864", "component_sku": "B", "quantity": 2}]},
            format="json",
        )
        self.assertEqual(upload.status_code, 200)
        self.assertEqual(upload.data["updated"], 1)


class ImportPamoWebCatalogCommandTests(TestCase):
    # SKU que "existen" en Shopify para estas pruebas (la API se simula).
    SHOPIFY_SKUS = {"pamo123", "pamo456", "7809", "5092", "DF-1001"}

    def _csv(self, directory, name, content):
        path = Path(directory) / name
        path.write_text(content, encoding="utf-8-sig")
        return str(path)

    def _run(self, products, kits):
        fake = lambda sku: "1" if sku in self.SHOPIFY_SKUS else None
        out = StringIO()
        with patch("products.management.commands.import_pamo_web_catalog.get_variant_by_sku", side_effect=fake):
            call_command("import_pamo_web_catalog", products=products, kits=kits, stdout=out)
        return out.getvalue()

    def test_imports_pamo_web_format_applying_the_cleanup_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            products = self._csv(directory, "productos.csv", "\n".join([
                "sku_sodimac;sku_pamo;ean",
                "54654;pamo123;7701234567890.0",
                "99999;;",
                "795744;7809;",
                "7809;795744;",   # fila invertida: se omite
                "5864;pamoX;",    # pamoX no existe en Shopify: gana el kit
                "390349;5092;",   # 5092 existe en Shopify: gana el producto
            ]))
            kits = self._csv(directory, "kits.csv", "\n".join([
                "kitnumber,ean,sku,quantity",
                "5864,7709,pamo123,1",
                "5864,7709,pamo456,2",
                "390349,,DF-1001,1",
                "777777,,pamo123,1",
                "777777,,NO-EXISTE,1",  # componente fuera de Shopify: kit descartado
            ]))
            output = self._run(products, kits)
            self._run(products, kits)  # repetir no duplica

        self.assertEqual(resolve_marketplace_sku(Marketplace.SODIMAC, "54654").sku, "pamo123")
        self.assertEqual(MarketplaceSku.objects.get(sku="54654").ean, "7701234567890")
        self.assertIn("99999", output)

        self.assertEqual(resolve_marketplace_sku(Marketplace.SODIMAC, "795744").sku, "7809")
        self.assertIsNone(resolve_marketplace_sku(Marketplace.SODIMAC, "7809"))
        self.assertFalse(Product.objects.filter(sku="795744").exists())

        kit = resolve_marketplace_sku(Marketplace.SODIMAC, "5864")
        self.assertTrue(kit.is_kit)
        self.assertEqual(MarketplaceSku.objects.get(sku="5864").ean, "7709")
        self.assertEqual(
            expand_product(kit, 1),
            [{"sku": "pamo123", "quantity": 1}, {"sku": "pamo456", "quantity": 2}],
        )

        self.assertEqual(resolve_marketplace_sku(Marketplace.SODIMAC, "390349").sku, "5092")
        self.assertFalse(Product.objects.filter(sku="390349").exists())

        self.assertIsNone(resolve_marketplace_sku(Marketplace.SODIMAC, "777777"))
        self.assertFalse(Product.objects.filter(sku="777777").exists())
        self.assertIn("NO-EXISTE", output)


UPLOAD = "products.functions.upload_sku_equivalences"


class _Token:
    """Cancela cuando `is_cancelled` se ha consultado `after` veces."""

    def __init__(self, after):
        self.after = after
        self.calls = 0

    def is_cancelled(self):
        self.calls += 1
        return self.calls > self.after

    def raise_if_cancelled(self):
        from orchestrator.core.cancellation import ProcessCancelledException

        raise ProcessCancelledException("cancelada")


def _upload_rows(rows, marketplace=Marketplace.SODIMAC):
    upload = SkuUpload.objects.create(marketplace=marketplace, rows=rows)
    return upload


def _run(upload, token=None):
    from .functions.upload_sku_equivalences import upload_sku_equivalences

    upload_sku_equivalences({"upload_id": upload.pk}, cancellation_token=token)
    upload.refresh_from_db()
    return upload


def _results(upload):
    return [(row["resultado"], row["resultado_codigo"]) for row in upload.results]


@patch(f"{UPLOAD}.get_variants_by_skus")
class UploadSkuEquivalencesTests(TestCase):
    def _shopify(self, mock, *skus):
        mock.side_effect = lambda block: {sku: f"v-{sku}" for sku in block if sku in skus}

    def test_invalid_rows_are_errors(self, shopify):
        self._shopify(shopify)
        upload = _run(
            _upload_rows(
                [
                    {"sku_pamo": "", "sku_marketplace": "1"},
                    {"sku_pamo": "P", "sku_marketplace": None},
                    {"sku_pamo": "P" * 65, "sku_marketplace": "2", "ean": "9" * 21},
                ]
            )
        )
        self.assertEqual(
            _results(upload),
            [
                ("Error: sku_pamo vacío", "error"),
                ("Error: sku_marketplace vacío", "error"),
                ("Error: sku_pamo supera 64 caracteres; ean supera 20 caracteres", "error"),
            ],
        )
        shopify.assert_not_called()

    def test_repeated_marketplace_sku_is_a_duplicate_of_the_first_row(self, shopify):
        self._shopify(shopify, "P", "Q")
        upload = _run(
            _upload_rows(
                [
                    {"sku_pamo": "P", "sku_marketplace": "1"},
                    {"sku_pamo": "P", "sku_marketplace": "1"},
                    {"sku_pamo": "Q", "sku_marketplace": "1.0"},
                ]
            )
        )
        self.assertEqual(
            _results(upload)[1:],
            [("Duplicado en el archivo (fila 2)", "error"), ("Duplicado en el archivo (fila 2)", "error")],
        )
        self.assertEqual(MarketplaceSku.objects.get().product.sku, "P")

    def test_kit_is_an_error(self, shopify):
        self._shopify(shopify, "K")
        _kit("K", ("A", 1))
        upload = _run(_upload_rows([{"sku_pamo": "K", "sku_marketplace": "1"}]))
        self.assertEqual(_results(upload), [("Es un kit: se gestiona en la carga de kits", "error")])
        self.assertFalse(MarketplaceSku.objects.exists())

    def test_block_that_fails_twice_does_not_stop_the_others(self, shopify):
        def flaky(block):
            if "BAD" in block:
                raise RuntimeError("throttled")
            return {sku: "v" for sku in block}

        shopify.side_effect = flaky
        with patch(f"{UPLOAD}.BATCH_SIZE", 1):
            upload = _run(
                _upload_rows([{"sku_pamo": "BAD", "sku_marketplace": "1"}, {"sku_pamo": "OK", "sku_marketplace": "2"}])
            )
        self.assertEqual(
            _results(upload),
            [("Error consultando Shopify, reintentar", "error"), ("Exitoso: producto y relación creados", "ok")],
        )
        self.assertEqual(shopify.call_count, 3)  # BAD dos veces, OK una

    def test_block_that_fails_once_is_retried(self, shopify):
        shopify.side_effect = [RuntimeError("timeout"), {"P": "v"}]
        upload = _run(_upload_rows([{"sku_pamo": "P", "sku_marketplace": "1"}]))
        self.assertEqual(_results(upload), [("Exitoso: producto y relación creados", "ok")])

    def test_not_in_shopify(self, shopify):
        self._shopify(shopify)
        Product.objects.create(sku="P")
        upload = _run(
            _upload_rows([{"sku_pamo": "P", "sku_marketplace": "1"}, {"sku_pamo": "NEW", "sku_marketplace": "2"}])
        )
        self.assertEqual(
            _results(upload),
            [("Alerta: existe en Pamo pero no en Shopify", "alert"), ("SKU no encontrado en Shopify", "error")],
        )
        self.assertFalse(MarketplaceSku.objects.exists())
        self.assertFalse(Product.objects.filter(sku="NEW").exists())

    def test_in_shopify_creates_product_and_relation_or_only_relation(self, shopify):
        self._shopify(shopify, "NEW", "OLD")
        Product.objects.create(sku="OLD")
        upload = _run(
            _upload_rows(
                [{"sku_pamo": "NEW", "sku_marketplace": "1", "ean": "77"}, {"sku_pamo": "OLD", "sku_marketplace": "2"}]
            )
        )
        self.assertEqual(
            _results(upload),
            [("Exitoso: producto y relación creados", "ok"), ("Exitoso: relación creada", "ok")],
        )
        self.assertEqual(MarketplaceSku.objects.get(sku="1").ean, "77")
        self.assertEqual(MarketplaceSku.objects.get(sku="2").product.sku, "OLD")

    def test_existing_relation_unchanged_or_ean_updated(self, shopify):
        self._shopify(shopify, "P")
        product = Product.objects.create(sku="P")
        MarketplaceSku.objects.create(product=product, marketplace=Marketplace.SODIMAC, sku="1", ean="77")
        MarketplaceSku.objects.create(product=product, marketplace=Marketplace.SODIMAC, sku="2", ean="77")
        MarketplaceSku.objects.create(product=product, marketplace=Marketplace.SODIMAC, sku="3", ean="77")
        upload = _run(
            _upload_rows(
                [
                    {"sku_pamo": "P", "sku_marketplace": "1", "ean": "77"},
                    {"sku_pamo": "P", "sku_marketplace": "2", "ean": ""},
                    {"sku_pamo": "P", "sku_marketplace": "3", "ean": "88"},
                ]
            )
        )
        self.assertEqual(
            _results(upload),
            [
                ("Sin cambios: la relación ya existía", "ok"),
                ("Sin cambios: la relación ya existía", "ok"),
                ("Exitoso: EAN actualizado", "ok"),
            ],
        )
        # EAN vacío no borra el existente.
        self.assertEqual(MarketplaceSku.objects.get(sku="2").ean, "77")
        self.assertEqual(MarketplaceSku.objects.get(sku="3").ean, "88")

    def test_marketplace_sku_pointing_to_another_product_is_reassigned(self, shopify):
        self._shopify(shopify, "NEW")
        MarketplaceSku.objects.create(
            product=Product.objects.create(sku="OLD"), marketplace=Marketplace.SODIMAC, sku="1", ean="77"
        )
        upload = _run(_upload_rows([{"sku_pamo": "NEW", "sku_marketplace": "1"}]))
        self.assertEqual(_results(upload), [("Reasignado: antes apuntaba a OLD", "alert")])
        equivalence = MarketplaceSku.objects.get()
        self.assertEqual((equivalence.product.sku, equivalence.ean), ("NEW", "77"))

    def test_same_marketplace_sku_in_another_marketplace_is_independent(self, shopify):
        self._shopify(shopify, "P")
        MarketplaceSku.objects.create(
            product=Product.objects.create(sku="OTHER"), marketplace=Marketplace.MADECENTRO, sku="1"
        )
        upload = _run(_upload_rows([{"sku_pamo": "P", "sku_marketplace": "1"}]))
        self.assertEqual(_results(upload), [("Exitoso: producto y relación creados", "ok")])
        self.assertEqual(MarketplaceSku.objects.get(marketplace=Marketplace.MADECENTRO).product.sku, "OTHER")

    def test_mixed_upload_summary_and_extra_columns_are_kept(self, shopify):
        self._shopify(shopify, "A", "B")
        Product.objects.create(sku="C")
        upload = _run(
            _upload_rows(
                [
                    {"sku_pamo": "A", "sku_marketplace": "1", "nota": "x"},
                    {"sku_pamo": "B", "sku_marketplace": 2.0},
                    {"sku_pamo": "C", "sku_marketplace": "3"},
                    {"sku_pamo": "", "sku_marketplace": "4"},
                ]
            )
        )
        self.assertEqual(upload.results[0]["nota"], "x")
        self.assertEqual(upload.results[1]["sku_marketplace"], 2.0)
        self.assertEqual(MarketplaceSku.objects.get(product__sku="B").sku, "2")
        self.assertEqual(upload.summary["total"], 4)
        self.assertEqual(upload.summary["por_codigo"], {"ok": 2, "alert": 1, "error": 1})
        self.assertEqual(
            upload.summary["por_caso"], {"producto_y_relacion": 2, "solo_en_pamo": 1, "invalido": 1}
        )
        self.assertIsNotNone(upload.finished_at)

    def test_cancellation_midway_keeps_partial_results(self, shopify):
        from orchestrator.core.cancellation import ProcessCancelledException

        self._shopify(shopify, "A", "B")
        upload = _upload_rows([{"sku_pamo": "A", "sku_marketplace": "1"}, {"sku_pamo": "B", "sku_marketplace": "2"}])
        # 1 consulta antes del bloque de Shopify, 1 antes de la fila A; cancela antes de B.
        with patch(f"{UPLOAD}.CANCEL_CHECK_EVERY", 1), self.assertRaises(ProcessCancelledException):
            _run(upload, token=_Token(after=2))
        upload.refresh_from_db()
        self.assertEqual(
            _results(upload),
            [("Exitoso: producto y relación creados", "ok"), ("No procesado: carga cancelada", "error")],
        )
        self.assertEqual(MarketplaceSku.objects.count(), 1)

    def test_unexpected_error_saves_the_report(self, shopify):
        self._shopify(shopify, "A")
        upload = _upload_rows([{"sku_pamo": "A", "sku_marketplace": "1"}])
        with patch(f"{UPLOAD}._apply", side_effect=RuntimeError("boom")), self.assertRaises(RuntimeError):
            _run(upload)
        upload.refresh_from_db()
        self.assertEqual(_results(upload), [("No procesado: la carga se detuvo por un error", "error")])

    def test_process_type_is_seeded(self, shopify):
        from orchestrator.models import ProcessType

        process_type = ProcessType.objects.get(code="products.upload_sku_equivalences")
        self.assertTrue(process_type.allow_concurrent)
        self.assertEqual(process_type.max_concurrent_global, 1)


class SkuUploadAPITests(APITestCase):
    def setUp(self):
        admin_group, _ = Group.objects.get_or_create(name="Admin")
        self.admin = User.objects.create_user("admin", email="admin@pamo.co")
        self.admin.groups.add(admin_group)
        self.other = User.objects.create_user("other")
        self.body = {"marketplace": "sodimac", "rows": [{"sku_pamo": "P", "sku_marketplace": "1"}]}

    @patch("products.apis.launch_process")
    def test_admin_launches_an_upload(self, launch):
        launch.return_value.pk = 77
        self.client.force_login(self.admin)

        response = self.client.post(reverse("products-sku-uploads"), self.body, format="json")

        self.assertEqual(response.status_code, 202)
        upload = SkuUpload.objects.get()
        self.assertEqual(response.data, {"id": upload.pk, "execution_id": 77})
        self.assertEqual((upload.execution_id, upload.uploaded_by, upload.marketplace), (77, self.admin, "sodimac"))
        launch.assert_called_once_with(
            code="products.upload_sku_equivalences", user=self.admin, params={"upload_id": upload.pk}
        )

    @patch("products.apis.launch_process")
    def test_rejects_without_session_or_role(self, launch):
        for user in (None, self.other):
            if user:
                self.client.force_login(user)
            for method, url in (
                ("post", reverse("products-sku-uploads")),
                ("get", reverse("products-sku-uploads")),
                ("get", reverse("products-sku-upload-detail", args=[1])),
            ):
                response = getattr(self.client, method)(url, self.body, format="json")
                self.assertEqual(response.status_code, 403, (user, method, url))
        launch.assert_not_called()

    @patch("products.apis.launch_process")
    def test_invalid_bodies_are_400(self, launch):
        self.client.force_login(self.admin)
        rows = self.body["rows"]
        for body in (
            {"marketplace": "amazon", "rows": rows},
            {"marketplace": "sodimac", "rows": []},
            {"marketplace": "sodimac", "rows": [{"sku_pamo": "P"}]},
        ):
            response = self.client.post(reverse("products-sku-uploads"), body, format="json")
            self.assertEqual(response.status_code, 400, body)
        with patch("products.serializers.SKU_UPLOAD_MAX_ROWS", 1):
            response = self.client.post(
                reverse("products-sku-uploads"), {"marketplace": "sodimac", "rows": rows * 2}, format="json"
            )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(SkuUpload.objects.exists())
        launch.assert_not_called()

    @patch("products.apis.get_execution_status")
    def test_detail_includes_execution_status_and_rows_when_done(self, execution_status):
        execution_status.return_value = {
            "status": "EJECUTANDO",
            "progress_percent": 40,
            "current_step": "Shopify: bloque 1 de 2",
            "error_message": None,
        }
        upload = SkuUpload.objects.create(marketplace="sodimac", rows=self.body["rows"], execution_id=5, uploaded_by=self.admin)
        self.client.force_login(self.admin)

        response = self.client.get(reverse("products-sku-upload-detail", args=[upload.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.data["status"], response.data["progress_percent"]), ("EJECUTANDO", 40))
        self.assertEqual(response.data["uploaded_by"], "admin@pamo.co")
        self.assertIsNone(response.data["rows"])
        execution_status.assert_called_once_with(5)

        upload.results = [{"sku_pamo": "P", "sku_marketplace": "1", "resultado": "x", "resultado_codigo": "ok"}]
        upload.save()
        self.assertEqual(self.client.get(reverse("products-sku-upload-detail", args=[upload.pk])).data["rows"], upload.results)

    def test_detail_404(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("products-sku-upload-detail", args=[999])).status_code, 404)

    @patch("products.apis.get_execution_status", return_value=None)
    def test_history_is_paginated_without_rows(self, execution_status):
        for _ in range(21):
            SkuUpload.objects.create(marketplace="sodimac", rows=self.body["rows"])
        self.client.force_login(self.admin)

        response = self.client.get(reverse("products-sku-uploads"))

        self.assertEqual(response.data["count"], 21)
        self.assertEqual(len(response.data["results"]), 20)
        self.assertNotIn("rows", response.data["results"][0])
        self.assertIsNone(response.data["results"][0]["status"])
