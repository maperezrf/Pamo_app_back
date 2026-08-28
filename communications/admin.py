from django.contrib import admin

from .models import WhatsAppAttempt, WhatsAppDraft, WhatsAppOutbox, WhatsAppWebhookEvent


admin.site.register(WhatsAppDraft)
admin.site.register(WhatsAppOutbox)
admin.site.register(WhatsAppAttempt)
admin.site.register(WhatsAppWebhookEvent)

