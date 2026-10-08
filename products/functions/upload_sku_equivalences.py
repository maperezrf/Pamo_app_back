from collections import Counter

from django.db import transaction
from django.utils import timezone

from integrations.shopify.functions.get_variants_by_skus import BATCH_SIZE, get_variants_by_skus

from ..models import MarketplaceSku, Product, SkuUpload
from .normalize_cell import normalize_cell

PAMO_COLUMN = "sku_pamo"
MARKETPLACE_COLUMN = "sku_marketplace"
EAN_COLUMN = "ean"
RESULT_COLUMN = "resultado"
CODE_COLUMN = "resultado_codigo"

OK, ALERT, ERROR = "ok", "alert", "error"
SHOPIFY_ATTEMPTS = 2
CANCEL_CHECK_EVERY = 25

MAX_LENGTHS = {
    PAMO_COLUMN: Product._meta.get_field("sku").max_length,
    MARKETPLACE_COLUMN: MarketplaceSku._meta.get_field("sku").max_length,
    EAN_COLUMN: MarketplaceSku._meta.get_field("ean").max_length,
}

# Caso → (código, texto). Los textos con datos de la fila se completan al
# asignarlos. El caso es la llave del resumen.
CASES = {
    "invalido": (ERROR, "Error: {detail}"),
    "duplicado": (ERROR, "Duplicado en el archivo (fila {row})"),
    "kit": (ERROR, "Es un kit: se gestiona en la carga de kits"),
    "error_shopify": (ERROR, "Error consultando Shopify, reintentar"),
    "solo_en_pamo": (ALERT, "Alerta: existe en Pamo pero no en Shopify"),
    "no_encontrado": (ERROR, "SKU no encontrado en Shopify"),
    "producto_y_relacion": (OK, "Exitoso: producto y relación creados"),
    "relacion_creada": (OK, "Exitoso: relación creada"),
    "sin_cambios": (OK, "Sin cambios: la relación ya existía"),
    "ean_actualizado": (OK, "Exitoso: EAN actualizado"),
    "reasignado": (ALERT, "Reasignado: antes apuntaba a {previous}"),
    "cancelado": (ERROR, "No procesado: carga cancelada"),
    "interrumpido": (ERROR, "No procesado: la carga se detuvo por un error"),
}


class _Cancelled(Exception):
    pass


def upload_sku_equivalences(params=None, progress_callback=None, cancellation_token=None):
    """Proceso registrado en orchestrator como
    `products.upload_sku_equivalences`. Carga equivalencias de SKU de UN
    marketplace (`SkuUpload` con id `params["upload_id"]`) validando cada
    `sku_pamo` contra Shopify. Ver
    docs/apps/products.md.

    1. Valida sin escribir: vacíos, largos, duplicados en el archivo y kits.
    2. Consulta Shopify por bloques (un reintento por bloque; si vuelve a
       fallar, sus filas quedan para reintentar y la carga sigue).
    3. Aplica fila por fila, cada una en su transacción: crea el `Product`
       si está en Shopify y no aquí, crea, reasigna o actualiza el
       `MarketplaceSku`, y escribe el EAN solo si viene con valor.

    Siempre guarda el reporte (`results`, `summary`, `finished_at`), también
    si se cancela o falla: las filas sin procesar quedan marcadas.
    """
    params = params or {}
    progress_callback = progress_callback or (lambda percent, step=None: None)
    upload = SkuUpload.objects.get(pk=params["upload_id"])
    rows = [_Row(index, raw) for index, raw in enumerate(upload.rows)]
    pending_case = "interrumpido"
    try:
        progress_callback(2, "Validando filas")
        _validate(rows)
        progress_callback(10, "Consultando Shopify")
        found = _lookup_shopify(rows, progress_callback, cancellation_token)
        progress_callback(60, "Aplicando cambios")
        _apply(rows, upload.marketplace, found, progress_callback, cancellation_token)
        pending_case = None
    except _Cancelled:
        pending_case = "cancelado"
        cancellation_token.raise_if_cancelled()
    finally:
        for row in rows:
            if row.case is None and pending_case:
                row.set(pending_case)
        upload.results = [row.as_result() for row in rows]
        upload.summary = _summary(rows)
        upload.finished_at = timezone.now()
        upload.save(update_fields=["results", "summary", "finished_at"])
    progress_callback(100, _summary_text(upload.summary))


class _Row:
    def __init__(self, index, raw):
        self.raw = raw
        # Número de fila en el Excel, contando el encabezado.
        self.number = index + 2
        self.sku_pamo = normalize_cell(raw.get(PAMO_COLUMN))
        self.sku_marketplace = normalize_cell(raw.get(MARKETPLACE_COLUMN))
        self.ean = normalize_cell(raw.get(EAN_COLUMN))
        self.case = None
        self.text = ""

    def set(self, case, **values):
        self.case = case
        self.text = CASES[case][1].format(**values)

    def as_result(self):
        return {**self.raw, RESULT_COLUMN: self.text, CODE_COLUMN: CASES[self.case][0]}


def _alive(rows):
    return [row for row in rows if row.case is None]


def _validate(rows):
    first_by_marketplace_sku = {}
    for row in rows:
        problems = []
        for column, value in ((PAMO_COLUMN, row.sku_pamo), (MARKETPLACE_COLUMN, row.sku_marketplace)):
            if not value:
                problems.append(f"{column} vacío")
        for column, value in ((PAMO_COLUMN, row.sku_pamo), (MARKETPLACE_COLUMN, row.sku_marketplace), (EAN_COLUMN, row.ean)):
            if len(value) > MAX_LENGTHS[column]:
                problems.append(f"{column} supera {MAX_LENGTHS[column]} caracteres")
        if problems:
            row.set("invalido", detail="; ".join(problems))
            continue
        # El mismo sku_marketplace solo puede aparecer una vez: repetido con
        # el mismo sku_pamo o hacia otro. Gana la primera fila.
        first = first_by_marketplace_sku.setdefault(row.sku_marketplace, row)
        if first is not row:
            row.set("duplicado", row=first.number)

    kits = set(
        Product.objects.filter(sku__in={row.sku_pamo for row in _alive(rows)}, is_kit=True).values_list(
            "sku", flat=True
        )
    )
    for row in _alive(rows):
        if row.sku_pamo in kits:
            row.set("kit")


def _lookup_shopify(rows, progress_callback, cancellation_token):
    skus = list(dict.fromkeys(row.sku_pamo for row in _alive(rows)))
    blocks = [skus[start : start + BATCH_SIZE] for start in range(0, len(skus), BATCH_SIZE)]
    found = {}
    failed = set()
    for index, block in enumerate(blocks):
        _check_cancelled(cancellation_token)
        for attempt in range(SHOPIFY_ATTEMPTS):
            try:
                found.update(get_variants_by_skus(block))
                break
            except Exception:  # se reintenta una vez; luego sus filas quedan para reintentar
                if attempt == SHOPIFY_ATTEMPTS - 1:
                    failed.update(block)
        progress_callback(10 + int(50 * (index + 1) / len(blocks)), f"Shopify: bloque {index + 1} de {len(blocks)}")
    for row in _alive(rows):
        if row.sku_pamo in failed:
            row.set("error_shopify")
    return found


def _apply(rows, marketplace, found, progress_callback, cancellation_token):
    alive = _alive(rows)
    for index, row in enumerate(alive):
        if index % CANCEL_CHECK_EVERY == 0:
            _check_cancelled(cancellation_token)
        try:
            _apply_row(row, marketplace, found)
        except Exception as error:  # un choque hace fallar solo esa fila
            row.set("invalido", detail=f"{type(error).__name__}: {error}")
        if index % CANCEL_CHECK_EVERY == 0 or index == len(alive) - 1:
            progress_callback(60 + int(39 * (index + 1) / len(alive)), f"Fila {index + 1} de {len(alive)}")


def _apply_row(row, marketplace, found):
    product = Product.objects.filter(sku=row.sku_pamo).first()
    if row.sku_pamo not in found:
        row.set("solo_en_pamo" if product else "no_encontrado")
        return

    with transaction.atomic():
        created_product = product is None
        if created_product:
            product = Product.objects.create(sku=row.sku_pamo)
        equivalence = (
            MarketplaceSku.objects.select_for_update()
            .select_related("product")
            .filter(marketplace=marketplace, sku=row.sku_marketplace)
            .first()
        )
        if equivalence is None:
            MarketplaceSku.objects.create(product=product, marketplace=marketplace, sku=row.sku_marketplace, ean=row.ean)
            row.set("producto_y_relacion" if created_product else "relacion_creada")
        elif equivalence.product_id != product.pk:
            previous = equivalence.product.sku
            equivalence.product = product
            # EAN vacío o ausente: no se toca el existente.
            if row.ean:
                equivalence.ean = row.ean
            equivalence.save(update_fields=["product", "ean"])
            row.set("reasignado", previous=previous)
        elif row.ean and row.ean != equivalence.ean:
            equivalence.ean = row.ean
            equivalence.save(update_fields=["ean"])
            row.set("ean_actualizado")
        else:
            row.set("sin_cambios")


def _check_cancelled(cancellation_token):
    if cancellation_token and cancellation_token.is_cancelled():
        raise _Cancelled()


def _summary(rows):
    return {
        "total": len(rows),
        "por_codigo": dict(Counter(CASES[row.case][0] for row in rows if row.case)),
        "por_caso": dict(Counter(row.case for row in rows if row.case)),
    }


def _summary_text(summary):
    codes = summary["por_codigo"]
    return f"{summary['total']} filas: {codes.get(OK, 0)} ok, {codes.get(ALERT, 0)} alertas, {codes.get(ERROR, 0)} errores"
