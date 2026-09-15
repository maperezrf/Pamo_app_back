"""Constantes de estado y configuración por defecto del orquestador."""


class ProcessStatus:
    # NOTA: los valores string (segundo elemento y los propios literales) se
    # mantienen sin traducir intencionalmente porque ya existen registros en
    # base de datos persistidos con estos valores exactos. Solo se traducen
    # los nombres de los atributos Python (identificadores), no los valores.
    PENDING = "PENDIENTE"
    QUEUED = "EN_COLA"
    RUNNING = "EJECUTANDO"
    COMPLETED = "COMPLETADO"
    ERROR = "ERROR"
    CANCELLING = "CANCELANDO"
    CANCELLED = "CANCELADO"
    INTERRUPTED = "INTERRUMPIDO"

    CHOICES = [
        (PENDING, "Pendiente"),
        (QUEUED, "En cola"),
        (RUNNING, "Ejecutando"),
        (COMPLETED, "Completado"),
        (ERROR, "Error"),
        (CANCELLING, "Cancelando"),
        (CANCELLED, "Cancelado"),
        (INTERRUPTED, "Interrumpido"),
    ]

    ACTIVE = (QUEUED, RUNNING, CANCELLING)


class ScheduleKind:
    ONCE_DATE = "ONCE_DATE"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    CRON = "CRON"

    CHOICES = [
        (ONCE_DATE, "Fecha específica"),
        (DAILY, "Diaria"),
        (WEEKLY, "Semanal"),
        (MONTHLY, "Mensual"),
        (CRON, "Expresión CRON"),
    ]


class Weekday:
    MONDAY = 0
    TUESDAY = 1
    WEDNESDAY = 2
    THURSDAY = 3
    FRIDAY = 4
    SATURDAY = 5
    SUNDAY = 6

    CHOICES = [
        (MONDAY, "Lunes"),
        (TUESDAY, "Martes"),
        (WEDNESDAY, "Miércoles"),
        (THURSDAY, "Jueves"),
        (FRIDAY, "Viernes"),
        (SATURDAY, "Sábado"),
        (SUNDAY, "Domingo"),
    ]


# Configuración por defecto si `settings.ORCHESTRATOR_SETTINGS` no la define.
DEFAULT_MAX_CONCURRENT_EXECUTIONS = 3
