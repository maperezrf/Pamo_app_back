from django.contrib import admin

from .models import (
    AuthorizedPerson,
    Remittance,
    RemittanceAuditEvent,
    RemittanceDelivery,
    RemittanceFavorite,
    RemittanceInvoiceAttempt,
    RemittanceLine,
    RemittanceParty,
    RemittanceSequence,
    RemittanceWarehouse,
)

admin.site.register([
    RemittanceWarehouse,
    RemittanceSequence,
    RemittanceParty,
    RemittanceFavorite,
    AuthorizedPerson,
    Remittance,
    RemittanceLine,
    RemittanceDelivery,
    RemittanceAuditEvent,
    RemittanceInvoiceAttempt,
])
