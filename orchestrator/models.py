from django.conf import settings
from django.db import models

from .core.constants import (
    ProcessStatus,
    ScheduleKind,
    Weekday,
)


class ProcessType(models.Model):
    """Catálogo declarativo de procesos ejecutables vía el orquestador.

    El callable real que ejecuta el proceso NO se guarda aquí (no se serializa
    código): se resuelve en runtime vía `core.registry`, poblado por cada app
    dueña del proceso en su propio `AppConfig.ready()` (ver
    `apps/orchestrator/registrations.py` para el caso de reports.f11).
    """

    code = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=150)
    app_label = models.CharField(max_length=100)
    allow_concurrent = models.BooleanField(default=False)
    max_concurrent_global = models.IntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Tipo de proceso"
        verbose_name_plural = "Tipos de proceso"

    def __str__(self):
        return self.name


class ProcessExecution(models.Model):
    """Una fila por ejecución (manual o programada) de un `ProcessType`."""

    process_type = models.ForeignKey(
        ProcessType, on_delete=models.CASCADE, related_name="executions"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="process_executions",
    )
    status = models.CharField(
        max_length=20,
        choices=ProcessStatus.CHOICES,
        default=ProcessStatus.PENDING,
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    progress_percent = models.IntegerField(default=0)
    current_step = models.CharField(max_length=255, null=True, blank=True)
    logs = models.TextField(blank=True, default="")
    error_message = models.TextField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    cancellation_requested = models.BooleanField(default=False)
    triggered_by_schedule = models.ForeignKey(
        "ProcessScheduleConfig",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="executions",
    )
    params = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Ejecución de proceso"
        verbose_name_plural = "Ejecuciones de proceso"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.process_type.code} #{self.pk} ({self.status})"

    def append_log(self, message):
        self.logs = f"{self.logs}{message}\n" if self.logs else f"{message}\n"


class ProcessScheduleConfig(models.Model):
    """Programación recurrente/puntual de un `ProcessType` (requerimiento 7)."""

    process_type = models.ForeignKey(
        ProcessType, on_delete=models.CASCADE, related_name="schedules"
    )
    schedule_kind = models.CharField(
        max_length=20, choices=ScheduleKind.CHOICES)
    run_at_date = models.DateField(null=True, blank=True)
    run_at_time = models.TimeField(null=True, blank=True)
    weekday = models.IntegerField(
        null=True, blank=True, choices=Weekday.CHOICES
    )
    day_of_month = models.IntegerField(null=True, blank=True)
    cron_expression = models.CharField(max_length=100, null=True, blank=True)
    params = models.JSONField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_schedules",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Programación de proceso"
        verbose_name_plural = "Programaciones de proceso"

    def __str__(self):
        return f"{self.process_type.code} - {self.schedule_kind}"
