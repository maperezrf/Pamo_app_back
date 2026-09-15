from django.contrib import admin

from .models import ProcessExecution, ProcessScheduleConfig, ProcessType


@admin.register(ProcessType)
class ProcessTypeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "app_label",
                    "allow_concurrent", "is_active")
    search_fields = ("code", "name", "app_label")


@admin.register(ProcessExecution)
class ProcessExecutionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "process_type",
        "user",
        "status",
        "progress_percent",
        "started_at",
        "finished_at",
    )
    list_filter = ("status", "process_type")
    readonly_fields = [f.name for f in ProcessExecution._meta.fields]


@admin.register(ProcessScheduleConfig)
class ProcessScheduleConfigAdmin(admin.ModelAdmin):
    list_display = ("process_type", "schedule_kind", "is_active", "created_by")
    list_filter = ("schedule_kind", "is_active")
