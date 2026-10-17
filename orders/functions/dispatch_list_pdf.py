from django.utils import timezone
from fpdf import FPDF
from fpdf.fonts import FontFace

from .label_links import links_expire_on

_HEADER_STYLE = FontFace(emphasis="BOLD", fill_color=(230, 230, 230))
_LINK_STYLE = FontFace(color=(0, 0, 238), emphasis="UNDERLINE")
# Pedido, Imagen, SKU, Producto, Cant., Destinatario, Guía (mm; A4
# horizontal con márgenes de 10: 277 de ancho útil).
_COLUMNS = (28, 22, 28, 70, 14, 79, 36)


def dispatch_list_pdf(location_name, orders):
    """PDF con el listado de pedidos que se manda a la bodega: una fila por
    producto con imagen, SKU, producto y cantidad; pedido (con su canal),
    destinatario y guía (link firmado, `label_links`) combinados por pedido.

    `orders`: [{"order_name", "channel", "recipient": [líneas], "items":
    [{"sku", "name", "quantity", "image"}], "tracking_number",
    "label_url"}]. `image` son los bytes de la imagen o None (ver
    `product_images`); `label_url` vacío si el pedido va sin guía (la
    bodega la genera). Devuelve los bytes del PDF.
    """
    now = timezone.localtime()
    pdf = FPDF(orientation="landscape", format="A4")
    pdf.set_margins(10, 10, 10)
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, _text(f"Despachos para {location_name}"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=9)
    pdf.cell(
        0, 6,
        _text(f"{len(orders)} pedido(s) - {now:%d/%m/%Y %H:%M}. Los links de las guías vencen el {links_expire_on()}."),
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.ln(2)

    with pdf.table(
        col_widths=_COLUMNS, headings_style=_HEADER_STYLE, line_height=6, text_align="LEFT", v_align="MIDDLE"
    ) as table:
        table.row(["Pedido", "Imagen", "SKU", "Producto", "Cant.", "Destinatario", "Guía"])
        for order in orders:
            items = order["items"] or [{"sku": "", "name": "(sin productos)", "quantity": "", "image": None}]
            span = len(items)
            for index, item in enumerate(items):
                row = table.row()
                if index == 0:
                    row.cell(_text(f"{order['order_name']}\n{order['channel']}"), rowspan=span)
                if item.get("image"):
                    row.cell(img=item["image"], img_fill_width=True)
                else:
                    row.cell("")
                row.cell(_text(item["sku"] or "-"))
                row.cell(_text(item["name"]))
                row.cell(str(item["quantity"]), align="CENTER")
                if index == 0:
                    row.cell(_text("\n".join(order["recipient"])), rowspan=span)
                    if order["label_url"]:
                        row.cell(_text(order["tracking_number"] or "Ver guía"), rowspan=span, link=order["label_url"], style=_LINK_STYLE)
                    else:
                        row.cell("Sin guía", rowspan=span)
    return bytes(pdf.output())


def _text(value):
    # Las fuentes base del PDF son Latin-1: tildes y ñ salen bien; otro
    # carácter (emoji, comillas tipográficas) se cambia por "?" en vez de fallar.
    return str(value).encode("latin-1", "replace").decode("latin-1")
