"""PDF de una cotización (draft order de Shopify), copiado tal cual de
`pamo_web/quote_print/pdf_generator.py` (reportlab). Posiciones en puntos;
A4 = 595.27 x 841.89 y el origen de reportlab es la esquina inferior
izquierda. Ver docs/apps/quotes.md.

Ajustar las constantes de LAYOUT si algún elemento se ve corrido.
"""
import io
import re
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from config.constants import SALES_PHONE
from integrations.shopify.functions.download_image import download_image

ASSETS_DIR = Path(__file__).resolve().parent.parent / "pdf_assets"

PAGE_W, PAGE_H = A4  # 595.27 x 841.89

# ── Layout ───────────────────────────────────────────────────────────────────
Y_COMPANY_TOP  = 778   # y de la primera línea de la empresa (arriba a la derecha)
Y_CUSTOMER_TOP = 695   # y de la primera línea del cliente
Y_TABLE_FIRST  = 618   # base del encabezado de la tabla en la hoja con el cliente
Y_TABLE_EXTRA  = 760   # base del encabezado de la tabla en las hojas siguientes
ROW_H          = 55    # alto de cada fila de producto
HEADER_H       = 20    # alto del encabezado de la tabla

LEFT_X         = 22    # borde izquierdo del contenido
RIGHT_X        = 574   # borde derecho del contenido
CUSTOMER_VAL_X = 175   # x donde empiezan los valores del cliente
TABLE_LEFT_X   = LEFT_X + 33  # borde izquierdo de la tabla (alineado con el cliente)

# Ancho de columnas en pt; suman RIGHT_X - TABLE_LEFT_X = 519.
COLS = [52, 68, 182, 52, 90, 75]   # FOTO  SKU  DESC  CANT  P.UNIT  TOTAL
assert sum(COLS) == RIGHT_X - TABLE_LEFT_X, f"COLS suma {sum(COLS)} != {RIGHT_X - TABLE_LEFT_X}"

ITEMS_PER_PAGE = 7
VALIDITY_DAYS  = 10    # "Plazo: 10 días"
IVA            = 1.19

# ── Pie de página ────────────────────────────────────────────────────────────
FOOTER_BOTTOM   = 30    # pt desde el borde inferior donde empieza el pie
FOOTER_GAP      = 10    # separación entre la línea del pie y su contenido
TOTALS_H_EST    = 180   # alto estimado (conservador) del bloque de totales
# ─────────────────────────────────────────────────────────────────────────────

CERTIFICATE_URL = "https://cdn.shopify.com/s/files/1/0617/8507/9974/files/Certificado_Bancolombia_Feprin_2026.pdf?v=1774303817"
RUT_URL = "https://cdn.shopify.com/s/files/1/0617/8507/9974/files/RUT_FEPRIN_2026.pdf?v=1774303720"


def quote_pdf(draft):
    """Arma el PDF de una cotización y devuelve (nombre de archivo, bytes).
    `draft` es lo que devuelve `integrations.shopify.get_draft_order`."""
    return f"cotizacion_{draft['name'].lstrip('#')}.pdf", _create_pdf(_quote_data(draft))


def _quote_data(draft):
    """Lo que `_get_draft_data` de pamo_web le pasaba al PDF: fecha de la
    última edición, vencimiento a 10 días, NIT y enlace de WhatsApp."""
    updated = parse_datetime(draft["updated_at"]) if draft["updated_at"] else None
    updated_on = timezone.localtime(updated).date() if updated else timezone.localdate()
    customer = draft["customer"] or {}
    # El metafield trae el número entre corchetes ("[900382733]"). Se lee por
    # clave: pamo_web borraba los dos primeros metafields antes de buscarlo y
    # perdía el NIT cuando quedaba en esas posiciones.
    document = re.search(r"\[(\d+)\]", customer.get("document", ""))
    number = draft["name"][1:]
    message = f"Hola, estoy viendo mi cotización {number} y tengo una consulta. ¿Me podrían ayudar, por favor?"
    return {
        "draft": draft,
        "update": updated_on.strftime("%d/%m/%Y"),
        "plazo": (updated_on + timedelta(days=VALIDITY_DAYS)).strftime("%d/%m/%Y"),
        "nit": document.group(1) if document else None,
        "url": f"https://api.whatsapp.com/send?phone={SALES_PHONE}&text={quote(message)}",
    }


def _discount_label(discount, list_total):
    """'Descuento (7%)'. Un descuento de valor fijo se pasa a porcentaje del
    precio de lista (con IVA, como el monto)."""
    if discount['value_type'] == 'PERCENTAGE':
        pct = float(discount['value'])
    elif list_total > 0:
        pct = round(float(discount['amount']) / list_total * 100)
    else:
        return 'Descuento'
    return f"Descuento ({pct:g}%)"


def _img_path(name):
    return str(ASSETS_DIR / name)


def _fetch_remote_img(url):
    """Foto del producto (CDN de Shopify, reducida) o None si falla."""
    try:
        return ImageReader(io.BytesIO(download_image(url)))
    except Exception:
        return None


def _fmt_cop(value):
    """Pesos colombianos: $ 1.234.567"""
    try:
        return "$ {:,.0f}".format(round(float(value))).replace(',', '.')
    except Exception:
        return str(value)


def _wrap_text(text, max_chars=36):
    """Corte de línea simple por palabras."""
    words, lines, cur = text.split(), [], ''
    for w in words:
        if len(cur) + len(w) + 1 <= max_chars:
            cur = (cur + ' ' + w).strip()
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _text(c, x, y, text, size=7.5, bold=False, color=colors.black):
    c.setFillColor(color)
    c.setFont('Helvetica-Bold' if bold else 'Helvetica', size)
    c.drawString(x, y, str(text))


def _text_right(c, x, y, text, size=7.5, bold=False, color=colors.black):
    c.setFillColor(color)
    c.setFont('Helvetica-Bold' if bold else 'Helvetica', size)
    c.drawRightString(x, y, str(text))


def _create_pdf(data):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    draft    = data['draft']
    plazo    = data['plazo']
    date_str = data['update']
    nit      = data['nit']
    items    = draft['line_items']

    # ── Totales ──────────────────────────────────────────────────────────────
    total     = float(draft['total'])
    subtotal  = total / IVA
    iva       = subtotal * (IVA - 1)
    bruto     = sum(float(i['unit_price']) * int(i['quantity']) for i in items) / IVA
    # Descuento de toda la cotización con el monto de Shopify, con IVA
    # (pamo_web lo recalculaba sin IVA).
    discount  = draft['discount'] if draft['discount'] and float(draft['discount']['amount']) > 0 else None

    # ── Alto de la imagen del pie, para saber si los totales caben ───────────
    _HELP_W = int((RIGHT_X - TABLE_LEFT_X) * 0.58)
    _help_h_est = 85
    try:
        _hr = ImageReader(_img_path('help.jpeg'))
        _hw, _hh = _hr.getSize()
        _help_h_est = _hh * (_HELP_W / _hw)
    except Exception:
        pass
    # los totales deben terminar sobre la línea del pie (+ 5 pt de margen)
    FOOTER_TOP_THRESHOLD = FOOTER_BOTTOM + _help_h_est + FOOTER_GAP + 5

    # Productos en grupos de 7 por hoja
    groups = [items[i:i + ITEMS_PER_PAGE] for i in range(0, max(len(items), 1), ITEMS_PER_PAGE)]

    def _draw_company_info():
        company_lines = [
            'FEPRIN SAS / PAMO.CO',
            'NIT: 900.382.733-3  Régimen Común',
            'Actividad Económica 4752  Tarifa Renta 0,40%',
            'BOGOTA - COLOMBIA',
            '',
            'No somos Autoretenedores - No somos Grandes Contribuyentes',
            'Régimen IVA: COMÚN  Actividad ICA: 47521  6,90x mil',
        ]
        y = Y_COMPANY_TOP
        gray = colors.HexColor('#555555')
        for line in company_lines:
            if line:
                _text_right(c, RIGHT_X, y, line, size=7, color=gray)
            y -= 9.5
        c.setFillColor(colors.black)

    def _background(name):
        c.setFillColor(colors.white)
        c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
        c.drawImage(_img_path(name), 0, 0, width=PAGE_W, height=PAGE_H, mask='auto')

    # ── Hoja 1 (portada) y 2 (¿Por qué Pamo?) ────────────────────────────────
    _background('Pg1.png')
    c.showPage()
    _background('Pg2.png')
    c.showPage()

    # ── Hojas de productos ───────────────────────────────────────────────────
    # y_row sobrevive al ciclo para ubicar los totales
    y_row = Y_TABLE_FIRST

    for page_idx, group in enumerate(groups):
        is_first = page_idx == 0
        is_last  = page_idx == len(groups) - 1

        # Primera hoja de productos → Pg3.png; las demás → Hoja-con-cintillo.png
        _background('Pg3.png' if is_first else 'Hoja-con-cintillo.png')

        if is_first:
            _draw_company_info()

            customer = draft['customer'] or {}
            y = Y_CUSTOMER_TOP

            def cust_field(label, value, y_pos):
                _text(c, LEFT_X + 33, y_pos, label, size=8, bold=True)
                _text(c, CUSTOMER_VAL_X, y_pos, value, size=8)
                return y_pos - 14

            if customer.get('display_name'):
                y = cust_field('Nombre / Empresa:', customer['display_name'].title(), y)
            if customer.get('email'):
                y = cust_field('Correo:', customer['email'], y)
            if nit:
                y = cust_field('Nit:', str(nit), y)
            phone = customer.get('phone', '')
            if phone:
                y = cust_field('Teléfono:', phone.replace("+57", ""), y)
            _text(c, LEFT_X + 33, y, f'Plazo: {VALIDITY_DAYS} días   Fecha de Vencimiento: {plazo}', size=8)
            _text_right(c, RIGHT_X, Y_CUSTOMER_TOP, f'Fecha:  {" / ".join(date_str.split("/"))} /', size=8)
            quote_num = draft['name'][1:]
            num_w = c.stringWidth(quote_num, 'Helvetica', 8)
            _text_right(c, RIGHT_X,         Y_CUSTOMER_TOP - 14, quote_num,         size=8, color=colors.red)
            _text_right(c, RIGHT_X - num_w, Y_CUSTOMER_TOP - 14, 'Cotización No. ', size=8)

        # ── Tabla de productos ────────────────────────────────────────────
        table_top = Y_TABLE_FIRST if is_first else Y_TABLE_EXTRA

        c.setFillColor(colors.HexColor("#f5f3f3"))
        c.rect(TABLE_LEFT_X, table_top, RIGHT_X - TABLE_LEFT_X, HEADER_H, fill=1, stroke=0)
        c.setFillColor(colors.black)

        headers = ['FOTO', 'SKU', 'DESCRIPCIÓN', 'CANTIDAD', 'P. UNITARIO', 'TOTAL']
        xh = TABLE_LEFT_X
        for h, w in zip(headers, COLS):
            _text(c, xh + 3, table_top + 6, h, size=6.5, bold=True)
            xh += w

        y_row = table_top
        for item in group:
            y_row -= ROW_H
            x = TABLE_LEFT_X

            # — Foto
            if item['image_url']:
                img_reader = _fetch_remote_img(item['image_url'])
                if img_reader:
                    try:
                        c.drawImage(img_reader, x + 2, y_row + 3,
                                    width=COLS[0] - 4, height=ROW_H - 6,
                                    preserveAspectRatio=True, mask='auto')
                    except Exception:
                        pass
            x += COLS[0]

            # — SKU (máx 14 caracteres)
            _text(c, x + 3, y_row + ROW_H - 13, item['sku'][:14], size=7)
            x += COLS[1]

            # — Descripción (hasta 3 líneas)
            for ln_i, ln in enumerate(_wrap_text(item['title'], 34)[:3]):
                _text(c, x + 3, y_row + ROW_H - 13 - ln_i * 9, ln, size=7)
            x += COLS[2]

            # — Cantidad
            _text(c, x + 3, y_row + ROW_H - 13, str(item['quantity']), size=7)
            x += COLS[3]

            # — Precio unitario (sin IVA)
            unit = float(item['unit_price']) / IVA
            _text(c, x + 3, y_row + ROW_H - 13, _fmt_cop(unit), size=7)
            x += COLS[4]

            # — Total de la línea
            _text(c, x + 3, y_row + ROW_H - 13, _fmt_cop(unit * int(item['quantity'])), size=7)

            c.setStrokeColor(colors.HexColor('#cccccc'))
            c.line(TABLE_LEFT_X, y_row, RIGHT_X, y_row)
            c.setStrokeColor(colors.black)

        # La última hoja queda abierta para los totales
        if not is_last:
            c.showPage()

    # ── Totales ──────────────────────────────────────────────────────────────
    sep_y = y_row - 12

    # Si no caben entre los productos y el pie → hoja nueva
    if sep_y - TOTALS_H_EST < FOOTER_TOP_THRESHOLD:
        c.showPage()
        _background('Hoja-con-cintillo.png')
        sep_y = Y_TABLE_EXTRA - 20

    c.setStrokeColor(colors.HexColor('#aaaaaa'))
    c.line(TABLE_LEFT_X, sep_y, RIGHT_X, sep_y)
    c.setStrokeColor(colors.black)

    # — Información de pago (columna izquierda)
    py = sep_y - 14
    _text(c, TABLE_LEFT_X, py,
          'Realizar el pago a nombre de FEPRIN SAS, NIT 900382733.',
          size=7, bold=True)
    py -= 11
    _text(c, TABLE_LEFT_X, py,
          'Bancolombia Cuenta de Ahorros No. 174-000021-78', size=7)

    # — Totales (columna derecha)
    label_x = RIGHT_X - 155
    ty = sep_y - 14

    def total_row(label, value, y_pos, bold_val=False):
        _text(c, label_x, y_pos, label, size=7.5, bold=True)
        _text_right(c, RIGHT_X, y_pos, value, size=7.5, bold=bold_val)
        return y_pos - 12

    ty = total_row('Total bruto', _fmt_cop(bruto), ty)
    if discount:
        ty = total_row(_discount_label(discount, bruto * IVA), _fmt_cop(discount['amount']), ty)
    ty = total_row('Subtotal', _fmt_cop(subtotal), ty)
    ty = total_row('IVA', _fmt_cop(iva), ty)
    ty = total_row('Total', _fmt_cop(total), ty, bold_val=True)

    ICON_GAP = 10
    totals_w = RIGHT_X - label_x

    # — Botón pagar
    pago_h = 28
    try:
        pago_reader = ImageReader(_img_path('Bot-pago.png'))
        nat_w, nat_h = pago_reader.getSize()
        pago_h = nat_h * (totals_w / nat_w)
        pago_y = ty - pago_h - 8
        c.drawImage(pago_reader, label_x, pago_y,
                    width=totals_w, height=pago_h, mask='auto')
        c.linkURL(draft['invoice_url'],
                  (label_x, pago_y, RIGHT_X, pago_y + pago_h), thickness=0)
    except Exception:
        pago_y = ty - pago_h - 8

    # — Descargas (certificación bancaria y RUT)
    dl_y = py - pago_h - 8
    lx = TABLE_LEFT_X
    for img_name, url in [('certificacion.png', CERTIFICATE_URL), ('rut.png', RUT_URL)]:
        try:
            reader = ImageReader(_img_path(img_name))
            nat_w, nat_h = reader.getSize()
            iw = nat_w * (pago_h / nat_h)
            c.drawImage(reader, lx, dl_y, width=iw, height=pago_h, mask='auto')
            c.linkURL(url, (lx, dl_y, lx + iw, dl_y + pago_h), thickness=0)
            lx += iw + ICON_GAP
        except Exception:
            pass

    # — Banner Bre-B
    links_y = min(dl_y, pago_y)
    try:
        breb_reader = ImageReader(_img_path('bre-b.png'))
        breb_nat_w, breb_nat_h = breb_reader.getSize()
        breb_w = RIGHT_X - TABLE_LEFT_X
        breb_h = breb_nat_h * (breb_w / breb_nat_w)
        breb_y = links_y - breb_h - 10
        c.drawImage(breb_reader, TABLE_LEFT_X, breb_y,
                    width=breb_w, height=breb_h, mask='auto')
    except Exception:
        pass

    # ── Pie de página, anclado al fondo de la última hoja ────────────────────
    HELP_W      = int((RIGHT_X - TABLE_LEFT_X) * 0.58)
    COL_GAP     = 10
    BTN_H       = 20
    text_x      = TABLE_LEFT_X + HELP_W + COL_GAP
    help_h = 85
    help_reader = None
    try:
        help_reader = ImageReader(_img_path('help.jpeg'))
        help_nat_w, help_nat_h = help_reader.getSize()
        help_h = help_nat_h * (HELP_W / help_nat_w)
    except Exception:
        pass

    help_y   = FOOTER_BOTTOM
    help_top = help_y + help_h

    footer_sep_y = help_top + FOOTER_GAP
    c.setStrokeColor(colors.HexColor('#aaaaaa'))
    c.line(TABLE_LEFT_X, footer_sep_y, RIGHT_X, footer_sep_y)
    c.setStrokeColor(colors.black)

    # — Imagen "¿Necesitas ayuda?"
    if help_reader:
        c.drawImage(help_reader, TABLE_LEFT_X, help_y,
                    width=HELP_W, height=help_h, mask='auto')

    # — Botones WhatsApp y teléfono sobre la imagen. En pamo_web el de
    # WhatsApp era un SVG (svglib); aquí es el mismo dibujo en PNG.
    empty_h = help_h * 0.45
    btn_y   = help_y + (empty_h - BTN_H) / 2
    BTN_GAP = 8
    start_x = 157

    buttons = [('wp.png', data['url']), ('telefono.png', f'tel:{SALES_PHONE}')]
    bx = start_x
    for img_name, url in buttons:
        try:
            reader = ImageReader(_img_path(img_name))
            nat_w, nat_h = reader.getSize()
            bw = nat_w * (BTN_H / nat_h)
            c.drawImage(reader, bx, btn_y, width=bw, height=BTN_H, mask='auto')
            c.linkURL(url, (bx, btn_y, bx + bw, btn_y + BTN_H), thickness=0)
            bx += bw + BTN_GAP
        except Exception:
            pass

    # — Políticas (columna derecha, alineada con el tope de la imagen)
    pol_y = help_top - 5
    _text(c, text_x, pol_y,
          'POLÍTICAS DE REEMBOLSO DE FEPRIN SAS', size=7, bold=True)
    pol_y -= 8
    _text(c, text_x, pol_y, 'Consulta nuestra politica de reembolso en:', size=7)
    pol_y -= 7
    _text(c, text_x, pol_y, 'https://www.pamo.co/policies/refund-policy', size=7,
          color=colors.HexColor('#1155CC'))
    pol_y -= 8
    policy_lines = [
        '',
        '*Todas las devoluciones y/o garantías estarán sujetas al derecho',
        'de retracto y a la política de garantías.',
        '',
        '*Toda solicitud debe notificarse a ventas@pamo.co dentro de 30',
        'días calendario desde la recepción del producto.',
        '',
        '*Si la garantía es procedente, se realizará la reparación gratuita.',
        'El producto debe venir correctamente embalado.',
    ]
    for ln in policy_lines:
        if ln:
            _text(c, text_x, pol_y, ln, size=7,
                  color=colors.HexColor('#333333'))
        pol_y -= 8

    c.showPage()
    c.save()
    return buf.getvalue()
