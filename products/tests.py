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
from .models import KitComponent, Marketplace, MarketplaceSku, Product


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
