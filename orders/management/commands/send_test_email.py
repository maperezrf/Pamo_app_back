import time

from django.conf import settings
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Envía un correo de prueba con la configuración EMAIL_* (canal de correo de los avisos de "
        "despacho). Sirve para comprobar el SMTP en local y en Railway."
    )

    def add_arguments(self, parser):
        parser.add_argument("to", help="Correo de destino de la prueba.")

    def handle(self, *args, **options):
        if not settings.EMAIL_HOST or not settings.DEFAULT_FROM_EMAIL:
            raise CommandError("Falta EMAIL_HOST o DEFAULT_FROM_EMAIL en la configuración.")
        self.stdout.write(
            f"Servidor {settings.EMAIL_HOST}:{settings.EMAIL_PORT} (TLS={settings.EMAIL_USE_TLS}), "
            f"usuario {'configurado' if settings.EMAIL_HOST_USER else 'VACÍO'}, desde {settings.DEFAULT_FROM_EMAIL}"
        )
        started = time.monotonic()
        try:
            EmailMessage(
                subject="Prueba de correo - Pamo backend",
                body="Correo de prueba del canal de avisos de despacho. Si llegó, el SMTP funciona.",
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[options["to"]],
            ).send(fail_silently=False)
        except Exception as error:  # noqa: BLE001 -- se muestra el motivo para diagnosticar
            elapsed = time.monotonic() - started
            hint = (
                " Un timeout suele ser el puerto SMTP bloqueado por el proveedor de hosting."
                if "timed out" in str(error).lower() or isinstance(error, TimeoutError)
                else ""
            )
            raise CommandError(f"No se pudo enviar ({type(error).__name__} tras {elapsed:.1f}s): {error}.{hint}") from error
        self.stdout.write(self.style.SUCCESS(f"Enviado a {options['to']} en {time.monotonic() - started:.1f}s."))
