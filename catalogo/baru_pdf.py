from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import hashlib
from pathlib import Path
import re

import pdfplumber


PRICE_PATTERN = re.compile(r"\d{1,3}(?:[.,]\d{3})+|\d+")


class BaruCatalogError(ValueError):
    pass


@dataclass(frozen=True)
class BaruCatalogRow:
    page: int
    row_on_page: int
    sku: str
    description: str
    raw_price: str
    gross_price: Decimal | None


def normalize_thousands_price(raw_price):
    value = str(raw_price or "").replace("$", "").replace(" ", "")
    if not PRICE_PATTERN.fullmatch(value):
        return None
    return Decimal(value.replace(".", "").replace(",", ""))


def derive_net_cost(gross_price, tax_rate):
    gross = Decimal(str(gross_price))
    rate = Decimal(str(tax_rate))
    if rate < 0:
        raise BaruCatalogError("La tasa de IVA no puede ser negativa.")
    return (gross / (Decimal("1") + rate / Decimal("100"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _grouped_text(words):
    lines = []
    for word in sorted(words, key=lambda item: (round(item["top"] / 2) * 2, item["x0"])):
        if not lines or abs(lines[-1][0] - word["top"]) > 2:
            lines.append([word["top"], [word["text"]]])
        else:
            lines[-1][1].append(word["text"])
    return " ".join(" ".join(line).strip() for _, line in lines).strip()


def extract_baru_catalog(pdf_path):
    source = Path(pdf_path).resolve()
    if not source.is_file():
        raise BaruCatalogError(f"No existe el PDF: {source}")

    rows = []
    with pdfplumber.open(source) as document:
        page_count = len(document.pages)
        for page_number, page in enumerate(document.pages, start=1):
            words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
            dollars = [word for word in words if word["x0"] > 480 and word["text"].strip().startswith("$")]
            centers = [word["top"] for word in dollars]
            for row_on_page, dollar in enumerate(dollars, start=1):
                index = row_on_page - 1
                lower = (centers[index - 1] + dollar["top"]) / 2 if index else max(0, dollar["top"] - 45)
                upper = (dollar["top"] + centers[index + 1]) / 2 if index + 1 < len(centers) else min(page.height, dollar["top"] + 45)
                price_words = [word for word in words if word["x0"] >= dollar["x0"] and abs(word["top"] - dollar["top"]) <= 2]
                raw_price = "".join(word["text"] for word in sorted(price_words, key=lambda item: item["x0"]))
                sku_words = [word for word in words if word["x0"] < 150 and lower <= word["top"] < upper and word["text"] != "CODIGO"]
                description_words = [word for word in words if 250 < word["x0"] < 480 and lower <= word["top"] < upper]
                rows.append(BaruCatalogRow(
                    page=page_number,
                    row_on_page=row_on_page,
                    sku=_grouped_text(sku_words).strip(),
                    description=_grouped_text(description_words).strip(),
                    raw_price=raw_price,
                    gross_price=normalize_thousands_price(raw_price),
                ))

    sku_counts = Counter(row.sku for row in rows if row.sku)
    def audit_row(row):
        return {
            "page": row.page,
            "row_on_page": row.row_on_page,
            "sku": row.sku,
            "description": row.description,
            "raw_price": row.raw_price,
            "gross_price": str(row.gross_price) if row.gross_price is not None else None,
        }

    audit = {
        "source_path": str(source),
        "source_filename": source.name,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "page_count": page_count,
        "extracted_rows": len(rows),
        "unique_skus": len(sku_counts),
        "duplicate_skus": sorted(sku for sku, count in sku_counts.items() if count > 1),
        "invalid_prices": [audit_row(row) for row in rows if row.gross_price is None],
        "missing_skus": [audit_row(row) for row in rows if not row.sku],
        "missing_descriptions": [audit_row(row) for row in rows if not row.description],
    }
    return rows, audit
