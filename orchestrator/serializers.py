from rest_framework import serializers

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

    class Meta:
        model = ProcessExecution
        fields = [
            "id",
            "process_type",
            "process_type_code",
            "user",
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


class ProcessScheduleConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProcessScheduleConfig
        fields = [
            "id",
            "process_type",
            "schedule_kind",
            "run_at_date",
            "run_at_time",
            "weekday",
            "day_of_month",
            "cron_expression",
            "params",
            "is_active",
            "created_by",
            "created_at",
        ]
        read_only_fields = ["id", "created_by", "created_at"]
