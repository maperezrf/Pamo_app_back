from django.contrib import admin

from .models import RemittanceInvoiceAttempt, RemittanceInvoiceLine

admin.site.register([RemittanceInvoiceLine, RemittanceInvoiceAttempt])
