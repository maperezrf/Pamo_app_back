import io
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


NAVY = colors.HexColor("#102A56")
MUTED = colors.HexColor("#697386")
BORDER = colors.HexColor("#CCD3DD")
TABLE_HEADER = colors.HexColor("#F1F3F6")
BOGOTA = ZoneInfo("America/Bogota")

DELIVERY_LABELS = {
    "PERSONAL_PICKUP": "Retira personalmente",
    "CARRIER": "Transportadora",
    "UBER": "Uber",
    "INDRIVE": "InDrive",
    "MESSENGER": "Mensajería / domiciliario",
    "OTHER": "Otro",
}


def _quantity(value):
    decimal = Decimal(value)
    if decimal == decimal.to_integral():
        return str(int(decimal))
    return format(decimal.normalize(), "f")


def _wrap(text, font_name, font_size, max_width):
    words = str(text or "").split()
    if not words:
        return [""]
    lines = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if stringWidth(candidate, font_name, font_size) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _draw_label_value(pdf, x, y, label, value):
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 9)
    pdf.drawString(x, y, label)
    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(x, y - 16, str(value or "—"))


def _draw_header(pdf, remittance, *, continued=False):
    width, height = LETTER
    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(46, height - 54, "PAMO COLOMBIA")
    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(46, height - 90, "REMISIÓN DIGITAL")
    pdf.setFont("Helvetica-Bold", 24)
    pdf.drawString(46, height - 118, remittance.number or "BORRADOR")
    if continued:
        pdf.setFont("Helvetica", 9)
        pdf.setFillColor(MUTED)
        pdf.drawString(170, height - 110, "Continuación")

    issued = remittance.confirmed_at or remittance.created_at or timezone.now()
    local_date = timezone.localtime(issued, BOGOTA)
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 9)
    pdf.drawString(width - 150, height - 54, "Fecha:")
    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica-Bold", 9)
    date_text = local_date.strftime("%d/%m/%Y")
    pdf.drawString(width - 150, height - 69, date_text)
    pdf.setStrokeColor(BORDER)
    pdf.line(46, height - 136, width - 46, height - 136)
    return height - 158


def _draw_footer(pdf):
    width, _ = LETTER
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 8)
    pdf.drawCentredString(width / 2, 28, "Documento generado electrónicamente por Pamo Maestro")


def build_remittance_pdf(remittance):
    """Genera el documento del cliente sin consultar ni dibujar datos contables privados."""
    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=LETTER, pageCompression=1)
    width, _ = LETTER
    y = _draw_header(pdf, remittance)

    _draw_label_value(pdf, 46, y, "Cliente:", remittance.customer.name)
    _draw_label_value(pdf, 46, y - 54, "NIT:", remittance.customer.nit)
    _draw_label_value(pdf, 320, y, "Solicitante / Recibe:", remittance.requester_name)
    method = DELIVERY_LABELS.get(remittance.delivery.method, remittance.delivery.method)
    _draw_label_value(pdf, 320, y - 54, "Método de entrega:", method)
    y -= 112
    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(46, y, "Mercancía")
    y -= 18
    columns = (46, 116, 385, width - 46)

    def table_header(current_y):
        pdf.setFillColor(TABLE_HEADER)
        pdf.rect(columns[0], current_y - 24, columns[-1] - columns[0], 24, fill=1, stroke=0)
        pdf.setStrokeColor(BORDER)
        pdf.rect(columns[0], current_y - 24, columns[-1] - columns[0], 24, fill=0, stroke=1)
        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(columns[0] + 8, current_y - 16, "Cantidad")
        pdf.drawString(columns[1] + 8, current_y - 16, "Descripción")
        pdf.drawString(columns[2] + 8, current_y - 16, "Destino de uso")
        return current_y - 24

    y = table_header(y)
    for line in remittance.lines.all():
        description_lines = _wrap(line.original_description, "Helvetica", 9, columns[2] - columns[1] - 16)
        destination_lines = _wrap(line.usage_destination or "—", "Helvetica", 9, columns[3] - columns[2] - 16)
        row_height = max(28, 13 * max(len(description_lines), len(destination_lines), 1) + 12)
        if y - row_height < 125:
            _draw_footer(pdf)
            pdf.showPage()
            y = table_header(_draw_header(pdf, remittance, continued=True) - 10)
        pdf.setStrokeColor(BORDER)
        pdf.rect(columns[0], y - row_height, columns[-1] - columns[0], row_height, fill=0, stroke=1)
        pdf.line(columns[1], y, columns[1], y - row_height)
        pdf.line(columns[2], y, columns[2], y - row_height)
        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica", 9)
        pdf.drawCentredString((columns[0] + columns[1]) / 2, y - 18, _quantity(line.quantity))
        for index, text in enumerate(description_lines):
            pdf.drawString(columns[1] + 8, y - 18 - index * 13, text)
        for index, text in enumerate(destination_lines):
            pdf.drawString(columns[2] + 8, y - 18 - index * 13, text)
        y -= row_height

    acceptance = getattr(remittance, "recipient_acceptance", None)
    if y < 185:
        _draw_footer(pdf)
        pdf.showPage()
        y = _draw_header(pdf, remittance, continued=True) - 12
    y -= 34
    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(46, y, "Firma de recibido:")
    signature_y = y - 92
    pdf.setStrokeColor(BORDER)
    pdf.rect(46, signature_y, 240, 76, fill=0, stroke=1)
    if acceptance:
        try:
            acceptance.signature_file.open("rb")
            signature = acceptance.signature_file.read()
            acceptance.signature_file.close()
            pdf.drawImage(
                ImageReader(io.BytesIO(signature)), 58, signature_y + 8,
                width=216, height=56, preserveAspectRatio=True, anchor="c", mask="auto",
            )
        except (OSError, ValueError):
            pdf.setFillColor(MUTED)
            pdf.setFont("Helvetica", 9)
            pdf.drawCentredString(166, signature_y + 38, "Firma registrada de forma privada")
        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(46, signature_y - 15, acceptance.signer_name)
    else:
        pdf.setFillColor(MUTED)
        pdf.setFont("Helvetica", 9)
        pdf.drawCentredString(166, signature_y + 38, "Pendiente de firma")

    delivery = remittance.delivery
    details = []
    if delivery.tracking_number:
        details.append(f"Guía: {delivery.tracking_number}")
    if delivery.provider_name:
        details.append(f"Entrega: {delivery.provider_name}")
    if delivery.notes:
        details.append(f"Observaciones: {delivery.notes}")
    if details:
        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica", 9)
        detail_y = signature_y + 60
        for detail in details[:4]:
            for wrapped in _wrap(detail, "Helvetica", 9, width - 350):
                pdf.drawString(320, detail_y, wrapped)
                detail_y -= 13

    _draw_footer(pdf)
    pdf.save()
    return output.getvalue()
