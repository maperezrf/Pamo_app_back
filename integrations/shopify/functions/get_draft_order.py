from ..client import ShopifyClient
from ..queries import GET_DRAFT_ORDER


def get_draft_order(draft_order_id):
    """Una cotización (draft order) por id (sin `gid://`) con sus líneas,
    o `None` si Shopify no la encuentra. La usa el PDF de la app `quotes`.

    Devuelve {"id", "name", "updated_at" (ISO, UTC), "invoice_url",
    "discount": {"title", "value", "value_type" (`PERCENTAGE` o
    `FIXED_AMOUNT`), "amount" (string, con IVA)} o None (descuento de
    toda la cotización), "total" (string), "customer": {"display_name",
    "email", "phone", "document"} o None, "line_items": [{"sku", "title",
    "quantity", "unit_price" (string, con IVA), "image_url"}]}. La imagen
    es la de la variante o, si no tiene (lo normal), la del producto. Un
    error de Shopify se propaga.
    """
    response = ShopifyClient().request_graphql(
        GET_DRAFT_ORDER, variables={"id": f"gid://shopify/DraftOrder/{draft_order_id}"}
    )
    draft = (response.get("data") or {}).get("draftOrder")
    if draft is None:
        return None
    customer = draft.get("customer")
    return {
        "id": str(draft_order_id),
        "name": draft.get("name") or "",
        "updated_at": draft.get("updatedAt") or "",
        "invoice_url": draft.get("invoiceUrl") or "",
        "discount": _discount(draft.get("appliedDiscount")),
        "total": _amount(draft.get("totalPriceSet")),
        "customer": customer
        and {
            "display_name": customer.get("displayName") or "",
            "email": customer.get("email") or draft.get("email") or "",
            "phone": (customer.get("defaultAddress") or {}).get("phone") or "",
            "document": (customer.get("document") or {}).get("value") or "",
        },
        "line_items": [_line(node) for node in (draft.get("lineItems") or {}).get("nodes") or []],
    }


def _line(node):
    image = (node.get("variant") or {}).get("image") or (node.get("product") or {}).get("featuredImage") or {}
    return {
        "sku": node.get("sku") or "",
        "title": node.get("title") or "",
        "quantity": node.get("quantity") or 0,
        "unit_price": _amount(node.get("originalUnitPriceSet")),
        "image_url": image.get("url") or "",
    }


def _discount(applied):
    if not applied:
        return None
    return {
        "title": applied.get("title") or "",
        "value": applied.get("value") or 0,
        "value_type": applied.get("valueType") or "",
        "amount": _amount(applied.get("amountSet")),
    }


def _amount(money_set):
    return ((money_set or {}).get("shopMoney") or {}).get("amount") or "0"
