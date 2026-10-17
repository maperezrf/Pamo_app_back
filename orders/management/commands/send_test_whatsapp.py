from django.core.management.base import BaseCommand, CommandError

from integrations.zernio.functions.list_whatsapp_templates import list_whatsapp_templates
from orders.functions.send_test_notification import TestNotificationError, send_test_whatsapp


class Command(BaseCommand):
    help = (
        "Prueba del canal WhatsApp (Zernio) de los avisos de despacho. --list muestra las plantillas de la cuenta "
        "(solo lectura). Con un número envía una plantilla aprobada (por defecto hello_world, en_US)."
    )

    def add_arguments(self, parser):
        parser.add_argument("to", nargs="?", help='Número en formato E.164 sin "+", ej. 573001234567.')
        parser.add_argument("--list", action="store_true", help="Listar las plantillas de la cuenta y salir.")
        parser.add_argument("--template", default="hello_world", help="Nombre de la plantilla aprobada.")
        parser.add_argument("--language", default="en_US", help="Idioma de la plantilla (ej. es_CO, en_US).")
        parser.add_argument("--param", action="append", default=[], help="Variable del cuerpo, en orden ({{1}}, {{2}}...).")

    def handle(self, *args, **options):
        if options["list"]:
            try:
                templates = list_whatsapp_templates()
            except Exception as error:  # noqa: BLE001 -- ej. API key inválida o cuenta no encontrada
                raise CommandError(f"No se pudieron leer las plantillas: {error}") from error
            for template in templates:
                header = f", encabezado {template['header_format']}" if template["header_format"] else ""
                self.stdout.write(
                    f"{template['name']} [{template['language']}] {template['status']} ({template['category']}, "
                    f"{template['body_variables']} variables{header}): {template['body'][:120]}"
                )
            return
        to = options["to"] or ""
        try:
            message_id = send_test_whatsapp(
                to, template=options["template"], language=options["language"], body_params=options["param"]
            )
        except TestNotificationError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(f"Enviado a {to}: {message_id}"))
