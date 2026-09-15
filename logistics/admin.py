from django.contrib import admin

from .models import (
    AuthorizedPerson,
    Remittance,
    RemittanceDelivery,
    RemittanceFavorite,
    RemittanceLine,
    RemittanceParty,
    RemittanceSequence,
    RemittanceState,
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
    RemittanceState,
])
