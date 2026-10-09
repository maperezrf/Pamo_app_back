from django.core.management.base import BaseCommand, CommandError

from orders.functions.send_test_notification import TestNotificationError, email_settings_summary, send_test_email


class Command(BaseCommand):
    help = (
        "Envía un correo de prueba con la configuración EMAIL_* (canal de correo de los avisos de "
        "despacho). Sirve para comprobar el SMTP en local y en Railway (allí también desde el admin: "
        "Bodegas de despacho → Probar avisos)."
    )

    def add_arguments(self, parser):
        parser.add_argument("to", help="Correo de destino de la prueba.")

    def handle(self, *args, **options):
        self.stdout.write(email_settings_summary())
        try:
            elapsed = send_test_email(options["to"])
        except TestNotificationError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(f"Enviado a {options['to']} en {elapsed:.1f}s."))
