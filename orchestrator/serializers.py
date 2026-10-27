from datetime import datetime

from django.utils import timezone
from rest_framework import serializers

from .core.constants import ScheduleKind
from .core.scheduler import get_next_run_time, is_valid_cron
from .models import ProcessExecution, ProcessScheduleConfig, ProcessType


class ProcessTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProcessType
        fields = [
            "id",
            "code",
            "name",
            "app_label",
            "allow_concurrent",
            "max_concurrent_global",
            "is_active",
        ]
        read_only_fields = fields


class ProcessExecutionSerializer(serializers.ModelSerializer):
    process_type_code = serializers.CharField(
        source="process_type.code", read_only=True
    )
    process_type_name = serializers.CharField(
        source="process_type.name", read_only=True
    )
    user_email = serializers.CharField(
        source="user.email", read_only=True, allow_null=True
    )

    class Meta:
        model = ProcessExecution
        fields = [
            "id",
            "process_type",
            "process_type_code",
            "process_type_name",
            "user",
            "user_email",
            "status",
            "started_at",
            "finished_at",
            "progress_percent",
            "current_step",
            "error_message",
            "duration_seconds",
            "cancellation_requested",
            "triggered_by_schedule",
            "params",
            "created_at",
        ]
        read_only_fields = fields


# Campos que usa cada tipo de programación; los demás se guardan vacíos.
SCHEDULE_REQUIRED_FIELDS = {
    ScheduleKind.ONCE_DATE: ["run_at_date", "run_at_time"],
    ScheduleKind.DAILY: ["run_at_time"],
    ScheduleKind.WEEKLY: ["weekday", "run_at_time"],
    ScheduleKind.MONTHLY: ["day_of_month", "run_at_time"],
    ScheduleKind.CRON: ["cron_expression"],
}
SCHEDULE_FIELDS = ["run_at_date", "run_at_time", "weekday", "day_of_month", "cron_expression"]


class ProcessScheduleConfigSerializer(serializers.ModelSerializer):
    process_type_code = serializers.CharField(
        source="process_type.code", read_only=True
    )
    process_type_name = serializers.CharField(
        source="process_type.name", read_only=True
    )
    next_run_at = serializers.SerializerMethodField()

    class Meta:
        model = ProcessScheduleConfig
        fields = [
            "id",
            "process_type",
            "process_type_code",
            "process_type_name",
            "schedule_kind",
            "run_at_date",
            "run_at_time",
            "weekday",
            "day_of_month",
            "cron_expression",
            "params",
            "is_active",
            "next_run_at",
            "created_by",
            "created_at",
        ]
        read_only_fields = ["id", "created_by", "created_at"]

    def get_next_run_at(self, schedule):
        return get_next_run_time(schedule.pk)

    def validate_process_type(self, process_type):
        if not process_type.is_active:
            raise serializers.ValidationError("El proceso está desactivado.")
        return process_type

    def validate_day_of_month(self, value):
        if value is not None and not 1 <= value <= 31:
            raise serializers.ValidationError("Debe estar entre 1 y 31.")
        return value

    def validate(self, attrs):
        # En un PATCH parcial se valida contra lo que ya está guardado.
        def current(field):
            if field in attrs:
                return attrs[field]
            return getattr(self.instance, field, None)

        kind = current("schedule_kind")
        errors = {
            field: "Obligatorio para este tipo de programación."
            for field in SCHEDULE_REQUIRED_FIELDS.get(kind, [])
            if current(field) in (None, "")
        }
        if errors:
            raise serializers.ValidationError(errors)

        if kind == ScheduleKind.CRON and not is_valid_cron(current("cron_expression")):
            raise serializers.ValidationError(
                {"cron_expression": "Expresión CRON inválida (5 campos: minuto hora día mes día-semana)."}
            )

        # Fecha única en el pasado: nunca se dispararía. Solo se revisa si
        # cambió la fecha/hora o el tipo, para poder pausar una ya disparada.
        moment_changed = {"schedule_kind", "run_at_date", "run_at_time"} & attrs.keys()
        if kind == ScheduleKind.ONCE_DATE and moment_changed and current("is_active") is not False:
            run_at = timezone.make_aware(
                datetime.combine(current("run_at_date"), current("run_at_time"))
            )
            if run_at <= timezone.now():
                raise serializers.ValidationError(
                    {"run_at_date": "La fecha y hora deben ser futuras."}
                )

        for field in SCHEDULE_FIELDS:
            if field not in SCHEDULE_REQUIRED_FIELDS[kind]:
                attrs[field] = None
        return attrs
