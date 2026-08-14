from django.contrib import admin

from .models import AllowedEmail


@admin.register(AllowedEmail)
class AllowedEmailAdmin(admin.ModelAdmin):
    list_display = ('email', 'notes', 'added_by', 'created_at')
    search_fields = ('email',)
    readonly_fields = ('created_at',)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.added_by = request.user
        super().save_model(request, obj, form, change)
