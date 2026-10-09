import re

from django.core.management.base import BaseCommand, CommandError

from integrations.whatsapp.functions.list_message_templates import list_message_templates
from integrations.whatsapp.functions.send_text_message import send_text_message
from orders.functions.send_test_notification import TestNotificationError, send_test_whatsapp


class Command(BaseCommand):
    help = (
        "Prueba del canal WhatsApp de los avisos de despacho. --list muestra las plantillas de la cuenta (solo "
        "lectura). Con un número envía una plantilla aprobada (por defecto hello_world, en_US) o, con --text, un "
        "texto libre (solo llega si ese número escribió a la línea en las últimas 24 h)."
    )

    def add_arguments(self, parser):
        parser.add_argument("to", nargs="?", help='Número en formato E.164 sin "+", ej. 573001234567.')
        parser.add_argument("--list", action="store_true", help="Listar las plantillas de la cuenta y salir.")
        parser.add_argument("--template", default="hello_world", help="Nombre de la plantilla aprobada.")
        parser.add_argument("--language", default="en_US", help="Idioma de la plantilla (ej. es_CO, en_US).")
        parser.add_argument("--param", action="append", default=[], help="Variable del cuerpo, en orden ({{1}}, {{2}}...).")
        parser.add_argument("--text", help="Enviar un texto libre en vez de una plantilla.")

    def handle(self, *args, **options):
        if options["list"]:
            try:
                templates = list_message_templates()
            except Exception as error:  # noqa: BLE001 -- ej. token vencido (código 190)
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
            if options["text"]:
                if not re.fullmatch(r"\d{10,15}", to):
                    raise TestNotificationError('Número inválido: va en formato E.164 sin "+" ni espacios, ej. 573001234567.')
                try:
                    message_id = send_text_message(to, options["text"])["message_id"]
                except Exception as error:  # noqa: BLE001 -- se muestra el motivo de Meta para diagnosticar
                    raise TestNotificationError(f"No se pudo enviar: {error}") from error
            else:
                message_id = send_test_whatsapp(
                    to, template=options["template"], language=options["language"], body_params=options["param"]
                )
        except TestNotificationError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(f"Enviado a {to}: {message_id}"))
