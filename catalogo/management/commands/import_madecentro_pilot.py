from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
from hashlib import sha256
from pathlib import Path
from posixpath import normpath
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from catalogo.channel_import import ChannelImportError, import_external_channel_snapshot
from catalogo.models import Channel


DEFAULT_SOURCE = Path(
    "/Users/mauricioperez/Documents/PAMO_APP/_local_sources/madecentro/2026-08-26/"
    "Madecentro_Piloto_Comercial_Utilizable.xlsx"
)
EXPECTED_HEADERS = [
    "SKU", "Producto", "Precio Madecentro", "Precio público sugerido", "Precio anterior",
    "Envío Bogotá / Cundinamarca", "Envío resto de Colombia", "Envío otros destinos",
    "Regla breve", "Estado",
]


class MadecentroPilotError(ValueError):
    pass


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _column_name(number):
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _cell(row, column):
    return f"{_column_name(column)}{row}"


def _load_xlsx(path):
    try:
        archive = ZipFile(path)
    except BadZipFile as error:
        raise MadecentroPilotError("El archivo Madecentro no es un XLSX válido.") from error
    with archive:
        shared_strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in shared_root.findall(f"{{{MAIN_NS}}}si"):
                shared_strings.append("".join(node.text or "" for node in item.iter(f"{{{MAIN_NS}}}t")))

        workbook_root = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        relationships_root = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {
            node.attrib["Id"]: node.attrib["Target"]
            for node in relationships_root.findall(f"{{{PKG_REL_NS}}}Relationship")
        }
        sheets = {}
        for node in workbook_root.findall(f".//{{{MAIN_NS}}}sheet"):
            relationship_id = node.attrib[f"{{{DOC_REL_NS}}}id"]
            target = targets[relationship_id]
            target_path = normpath(target.lstrip("/")) if target.startswith("/xl/") else normpath(f"xl/{target}")
            root = ElementTree.fromstring(archive.read(target_path))
            cells = {}
            max_row = 0
            for cell_node in root.findall(f".//{{{MAIN_NS}}}c"):
                reference = cell_node.attrib.get("r", "")
                row_digits = "".join(character for character in reference if character.isdigit())
                max_row = max(max_row, int(row_digits or 0))
                cell_type = cell_node.attrib.get("t", "")
                formula_node = cell_node.find(f"{{{MAIN_NS}}}f")
                value_node = cell_node.find(f"{{{MAIN_NS}}}v")
                if cell_type == "inlineStr":
                    value = "".join(text.text or "" for text in cell_node.iter(f"{{{MAIN_NS}}}t"))
                elif value_node is None:
                    value = None
                elif cell_type == "s":
                    value = shared_strings[int(value_node.text)]
                elif cell_type in {"str", "e"}:
                    value = value_node.text or ""
                elif cell_type == "b":
                    value = value_node.text == "1"
                else:
                    try:
                        value = Decimal(value_node.text)
                    except (InvalidOperation, TypeError):
                        value = value_node.text
                cells[reference] = {
                    "value": value,
                    "formula": f"={formula_node.text}" if formula_node is not None and formula_node.text else "",
                }
            sheets[node.attrib["name"]] = {"cells": cells, "max_row": max_row}
        return sheets


def _text(value):
    result = str(value or "").strip()
    if result.endswith(".0") and result[:-2].isdigit():
        return result[:-2]
    return result


def _decimal(value, label):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError) as error:
        raise MadecentroPilotError(f"{label} debe ser numérico.") from error


def _money_equal(actual, expected):
    return _decimal(actual, "Valor calculado").quantize(Decimal("1")) == expected.quantize(Decimal("1"))


def read_madecentro_pilot(path):
    workbook = _load_xlsx(path)
    required_sheets = {"Propuesta Madecentro", "Condiciones"}
    if not required_sheets.issubset(workbook):
        raise MadecentroPilotError("El libro debe contener Propuesta Madecentro y Condiciones.")

    proposal = workbook["Propuesta Madecentro"]
    conditions = workbook["Condiciones"]
    proposal_value = lambda row, column: proposal["cells"].get(_cell(row, column), {}).get("value")
    proposal_formula = lambda row, column: proposal["cells"].get(_cell(row, column), {}).get("formula", "")
    condition_value = lambda coordinate: conditions["cells"].get(coordinate, {}).get("value")
    headers = [_text(proposal_value(6, column)) for column in range(1, 11)]
    if headers != EXPECTED_HEADERS:
        raise MadecentroPilotError("Los encabezados del piloto Madecentro no coinciden con el contrato esperado.")

    policy = {
        "madecentro_discount_percent": _decimal(condition_value("B5"), "Margen Madecentro"),
        "public_price_source": _text(condition_value("B6")),
        "previous_price_discount_percent": _decimal(condition_value("B7"), "Descuento anterior"),
        "previous_price_rounding_cop": _decimal(condition_value("B8"), "Redondeo"),
        "bogota_cundinamarca_fee_cop": _decimal(condition_value("B12"), "Tarifa Bogotá/Cundinamarca"),
        "bogota_cundinamarca_free_from_cop": _decimal(condition_value("C12"), "Umbral Bogotá/Cundinamarca"),
        "colombia_fee_cop": _decimal(condition_value("B13"), "Tarifa resto de Colombia"),
        "colombia_free_from_cop": _decimal(condition_value("C13"), "Umbral resto de Colombia"),
        "other_destinations_fee_cop": _decimal(condition_value("B14"), "Tarifa otros destinos"),
    }
    if policy["public_price_source"].casefold() != "shopify vigente":
        raise MadecentroPilotError("La fuente del precio público debe declarar Shopify vigente.")

    warning = _text(condition_value("A17"))
    records = []
    seen = set()
    for row_number in range(7, proposal["max_row"] + 1):
        values = [proposal_value(row_number, column) for column in range(1, 11)]
        sku = _text(values[0])
        if not sku:
            continue
        if sku.casefold() in seen:
            raise MadecentroPilotError(f"El SKU {sku} está repetido en el archivo.")
        seen.add(sku.casefold())
        workbook_status = _text(values[9])
        is_pilot = workbook_status.startswith("PILOTO")
        is_blocked = workbook_status.startswith("BLOQUEADO")
        if not (is_pilot or is_blocked):
            raise MadecentroPilotError(f"Fila {row_number}: estado Madecentro no reconocido.")

        commercial_values = values[2:8]
        if is_pilot:
            if any(not proposal_formula(row_number, column).startswith("=") for column in (3, 5, 6, 7, 8)):
                raise MadecentroPilotError(f"Fila {row_number}: faltan fórmulas comerciales esperadas.")
            madecentro_price, public_price, previous_price, bogota_fee, colombia_fee, other_fee = [
                _decimal(value, f"Fila {row_number}") for value in commercial_values
            ]
            expected_madecentro = (public_price * (Decimal("1") - policy["madecentro_discount_percent"])).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP,
            )
            previous_raw = public_price / (Decimal("1") - policy["previous_price_discount_percent"])
            rounding = policy["previous_price_rounding_cop"]
            expected_previous = (previous_raw / rounding).quantize(Decimal("1"), rounding=ROUND_CEILING) * rounding
            expected_bogota = Decimal("0") if public_price >= policy["bogota_cundinamarca_free_from_cop"] else policy["bogota_cundinamarca_fee_cop"]
            expected_colombia = Decimal("0") if public_price >= policy["colombia_free_from_cop"] else policy["colombia_fee_cop"]
            expected_other = policy["other_destinations_fee_cop"]
            checks = zip(
                (madecentro_price, previous_price, bogota_fee, colombia_fee, other_fee),
                (expected_madecentro, expected_previous, expected_bogota, expected_colombia, expected_other),
            )
            if not all(_money_equal(actual, expected) for actual, expected in checks):
                raise MadecentroPilotError(f"Fila {row_number}: los valores no corresponden a las condiciones del piloto.")
        else:
            madecentro_price = public_price = previous_price = bogota_fee = colombia_fee = other_fee = None

        records.append({
            "external_product_id": f"MADECENTRO-PILOT:{sku}",
            "sku": sku,
            "title": _text(values[1]),
            "state": "PILOT_MARGIN_PENDING" if is_pilot else "BLOCKED_NO_SHOPIFY",
            "price": str(madecentro_price) if madecentro_price is not None else None,
            "currency": "COP",
            "payload": {
                "classification": "COMMERCIAL_PILOT_NOT_LIVE_CHANNEL",
                "workbook_status": workbook_status,
                "public_suggested_price": str(public_price) if public_price is not None else None,
                "previous_reference_price": str(previous_price) if previous_price is not None else None,
                "shipping": {
                    "bogota_cundinamarca": str(bogota_fee) if bogota_fee is not None else None,
                    "rest_of_colombia": str(colombia_fee) if colombia_fee is not None else None,
                    "other_destinations": str(other_fee) if other_fee is not None else None,
                },
                "rule": _text(values[8]),
                "commercial_policy": {key: str(value) for key, value in policy.items()},
                "margin_warning": warning,
                "source_row": row_number,
            },
        })
    if not records:
        raise MadecentroPilotError("El piloto Madecentro no contiene productos.")
    return records


class Command(BaseCommand):
    help = "Valida e importa la propuesta comercial Madecentro únicamente a SQLite local."

    def add_arguments(self, parser):
        parser.add_argument("--source", default=str(DEFAULT_SOURCE))

    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            raise CommandError("Este importador solo puede escribir en SQLite local.")
        source = Path(options["source"]).expanduser().resolve()
        if not source.is_file():
            raise CommandError(f"No existe el archivo Madecentro: {source}")
        try:
            records = read_madecentro_pilot(source)
            digest = sha256(source.read_bytes()).hexdigest()
            summary = import_external_channel_snapshot(
                Channel.MADECENTRO,
                records,
                complete=True,
                source=f"XLSX local {source.name} sha256={digest}",
            )
        except (MadecentroPilotError, ChannelImportError) as error:
            raise CommandError(str(error)) from error
        cache.clear()
        self.stdout.write(self.style.SUCCESS(
            f"Madecentro → SQLite local: {summary['total']} registros; {summary['exact']} exactos, "
            f"{summary['ambiguous']} ambiguos, {summary['missing_shopify']} ausentes; "
            f"{summary['linked_master_rows']} vínculos maestros; externalWrites=0."
        ))
