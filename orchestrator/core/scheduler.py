"""Wrapper sobre APScheduler para el scheduler de procesos (requerimiento 7).

Arranca desde `OrchestratorConfig.ready()`. Traduce cada
`ProcessScheduleConfig` activo a un trigger de APScheduler y, al disparar,
reutiliza el mismo flujo de `launch_process` (cola/duplicados incluidos).
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from .constants import ScheduleKind

logger = logging.getLogger(__name__)

_scheduler = BackgroundScheduler(timezone="America/Bogota")
_started = False


def _job_id(schedule_id):
    return f"orchestrator-schedule-{schedule_id}"


def _build_trigger(schedule):
    if schedule.schedule_kind == ScheduleKind.ONCE_DATE:
        run_date = None
        if schedule.run_at_date and schedule.run_at_time:
            from datetime import datetime

            run_date = datetime.combine(
                schedule.run_at_date, schedule.run_at_time)
        return DateTrigger(run_date=run_date)

    if schedule.schedule_kind == ScheduleKind.DAILY:
        return CronTrigger(
            hour=schedule.run_at_time.hour if schedule.run_at_time else 0,
            minute=schedule.run_at_time.minute if schedule.run_at_time else 0,
        )

    if schedule.schedule_kind == ScheduleKind.WEEKLY:
        return CronTrigger(
            day_of_week=schedule.weekday if schedule.weekday is not None else 0,
            hour=schedule.run_at_time.hour if schedule.run_at_time else 0,
            minute=schedule.run_at_time.minute if schedule.run_at_time else 0,
        )

    if schedule.schedule_kind == ScheduleKind.MONTHLY:
        return CronTrigger(
            day=schedule.day_of_month or 1,
            hour=schedule.run_at_time.hour if schedule.run_at_time else 0,
            minute=schedule.run_at_time.minute if schedule.run_at_time else 0,
        )

    if schedule.schedule_kind == ScheduleKind.CRON:
        return CronTrigger.from_crontab(schedule.cron_expression)

    raise ValueError(f"schedule_kind no soportado: {schedule.schedule_kind}")


def _run_schedule(schedule_id):
    from ..models import ProcessScheduleConfig
    from ..services import launch_process

    try:
        schedule = ProcessScheduleConfig.objects.select_related("process_type").get(
            pk=schedule_id, is_active=True
        )
    except ProcessScheduleConfig.DoesNotExist:
        return

    launch_process(
        code=schedule.process_type.code,
        user=None,
        params=schedule.params or {},
        triggered_by_schedule=schedule,
    )


def register_schedule(schedule):
    """Registra (o re-registra) el job de una programación en caliente."""
    job_id = _job_id(schedule.pk)
    if _scheduler.get_job(job_id):
        _scheduler.remove_job(job_id)

    if not schedule.is_active:
        return

    trigger = _build_trigger(schedule)
    _scheduler.add_job(
        _run_schedule,
        trigger=trigger,
        args=[schedule.pk],
        id=job_id,
        replace_existing=True,
    )


def unregister_schedule(schedule_id):
    job_id = _job_id(schedule_id)
    if _scheduler.get_job(job_id):
        _scheduler.remove_job(job_id)


def start_scheduler():
    global _started
    if _started:
        return

    from ..models import ProcessScheduleConfig

    if not _scheduler.running:
        _scheduler.start()

    for schedule in ProcessScheduleConfig.objects.filter(is_active=True):
        try:
            register_schedule(schedule)
        except Exception:  # noqa: BLE001
            logger.exception(
                "No se pudo registrar la programación #%s", schedule.pk
            )

    _started = True
