import re
import time

from django.conf import settings
from django.core.mail import EmailMessage

from integrations.whatsapp.functions.send_template_message import send_template_message


class TestNotificationError(Exception):
    """La prueba no se pudo enviar; el texto es para quien prueba."""


def send_test_email(to):
    """Correo de prueba con la configuración EMAIL_* (canal de correo de los
    avisos de despacho). Lo usan el comando `send_test_email` y la página
    "Probar avisos" del admin. Devuelve los segundos que tardó."""
    if not settings.EMAIL_HOST or not settings.DEFAULT_FROM_EMAIL:
        raise TestNotificationError("Falta EMAIL_HOST o DEFAULT_FROM_EMAIL en la configuración.")
    started = time.monotonic()
    try:
        EmailMessage(
            subject="Prueba de correo - Pamo backend",
            body="Correo de prueba del canal de avisos de despacho. Si llegó, el SMTP funciona.",
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[to],
        ).send(fail_silently=False)
    except Exception as error:  # noqa: BLE001 -- se muestra el motivo para diagnosticar
        elapsed = time.monotonic() - started
        hint = (
            " Un timeout suele ser el puerto SMTP bloqueado por el proveedor de hosting."
            if "timed out" in str(error).lower() or isinstance(error, TimeoutError)
            else ""
        )
        raise TestNotificationError(f"No se pudo enviar ({type(error).__name__} tras {elapsed:.1f}s): {error}.{hint}") from error
    return time.monotonic() - started


def email_settings_summary():
    """Qué configuración de correo hay, sin mostrar la contraseña."""
    return (
        f"Servidor {settings.EMAIL_HOST or 'VACÍO'}:{settings.EMAIL_PORT} (TLS={settings.EMAIL_USE_TLS}), "
        f"usuario {'configurado' if settings.EMAIL_HOST_USER else 'VACÍO'}, "
        f"desde {settings.DEFAULT_FROM_EMAIL or 'VACÍO'}"
    )


def send_test_whatsapp(to, *, template="hello_world", language="en_US", body_params=()):
    """Plantilla aprobada de prueba (por defecto `hello_world`, en_US).
    `to` en E.164 sin "+". Devuelve el id del mensaje en Meta."""
    if not re.fullmatch(r"\d{10,15}", to or ""):
        raise TestNotificationError('Número inválido: va en formato E.164 sin "+" ni espacios, ej. 573001234567.')
    try:
        result = send_template_message(to, template, language=language, body_params=body_params)
    except Exception as error:  # noqa: BLE001 -- se muestra el motivo de Meta para diagnosticar
        raise TestNotificationError(f"No se pudo enviar: {error}") from error
    return result["message_id"]
