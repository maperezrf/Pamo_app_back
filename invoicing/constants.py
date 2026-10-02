from decimal import Decimal

# Valores de negocio fijos para facturar a Sodimac en Siigo, copiados de
# pamo_web (SigoConnection.create_invoice). No son secretos. Ver
# docs/apps/invoicing.md.

# Mientras se prueba, la factura NO se timbra ante la DIAN ni se envía por
# correo (decidido 2026-09-25). Pasar a timbrar = cambiar estas dos
# constantes a True (en pamo_web ambas eran True).
SODIMAC_STAMP_SEND = False
SODIMAC_MAIL_SEND = False

SODIMAC_DOCUMENT_ID = 26647
SODIMAC_COST_CENTER = 116
SODIMAC_SELLER = 643
SODIMAC_RETENTION_IDS = [13457, 13464]
SODIMAC_PAYMENT_ID = 6507
SODIMAC_ITEM_TAXES = [{"id": 16104}, {"id": 13456}]

IVA_RATE = Decimal("0.19")
RETEIVA_RATE = Decimal("0.15")  # sobre el IVA
RETEICA_RATE = Decimal("0.01104")
RETEFUENTE_RATE = Decimal("0.025")

SODIMAC_CUSTOMER = {
    "person_type": "Company",
    "id_type": "31",
    "identification": "800242106",
    "branch_office": 0,
    "name": ["SODIMAC COLOMBIA S A"],
    "address": {
        "address": "CR 68 D 80 70",
        "city": {
            "country_code": "Co",
            "country_name": "Colombia",
            "state_code": "11",
            "city_code": "11001",
            "city_name": "Bogotá",
        },
        "postal_code": "110911",
    },
    "phones": [{"indicative": "601", "number": "5460000", "extension": "000"}],
    "contacts": [
        {
            "first_name": "SODIMAC",
            "last_name": "COLOMBIA S A",
            "email": "recepcionfacturaelectronicasodimac2@homecenter.co",
            "phone": {"indicative": "000", "number": "0000000"},
        },
        {
            "first_name": "FACTURACION",
            "last_name": "",
            "email": "recepcionfacturaelectronicasodimac2@homecenter.co",
            "phone": {},
        },
        {"first_name": "marketplace", "last_name": "@pamo.co", "email": "marketplace@pamo.co", "phone": {}},
        {"first_name": "LIDERCOMERCAIL1", "last_name": "@PAMO.CO", "email": "LIDERCOMERCAIL1@PAMO.CO", "phone": {}},
        {
            "first_name": "Jeffry",
            "last_name": "Herrera",
            "email": "jherreram@homecenter.co",
            "phone": {"indicative": "57", "number": "3155549249"},
        },
        {"first_name": "omunoz", "last_name": "@homecenter.co", "email": "omunoz@homecenter.co", "phone": {}},
        {"first_name": "contabilidad", "last_name": "@feprin.com", "email": "contabilidad@feprin.com", "phone": {}},
        {
            "first_name": "facturacionelectronica",
            "last_name": "@feprin.com",
            "email": "facturacionelectronica@feprin.com",
            "phone": {},
        },
    ],
}
SODIMAC_CUSTOMER_NAME = "SODIMAC COLOMBIA S A"
