from ..client import SodimacRpaClient

VIEWER_PATH = "/vnc/vnc.html?autoconnect=true&resize=scale"


def get_status():
    """Estado del RPA (`GET /status`): `state` (INICIANDO, LOGIN_HOME,
    REDIRECCION_GOOGLE, VALIDACION_HUMANA, AUTENTICADO, ERROR o SIN_INICIAR),
    `message`, `updated_at`, `running` y `pending_intervention` (validación
    humana pendiente o null)."""
    return SodimacRpaClient().get("/status")


def continue_run():
    """Libera la pausa por validación humana (`POST /continue`), después de
    resolver el CAPTCHA en el visor. `SodimacRpaBusy` (409) si no hay
    ninguna pendiente."""
    return SodimacRpaClient().post("/continue")


def kill_run():
    """Detiene la corrida en curso (`POST /kill`): cierra Chrome y el visor.
    `SodimacRpaBusy` (409) si no hay ninguna corrida."""
    return SodimacRpaClient().post("/kill")


def viewer_url(base_url):
    """Visor noVNC del RPA: el mismo Chrome que controla Selenium. En Railway
    se enciende al iniciar el visor del RPA (hoy, al aparecer un CAPTCHA)."""
    return f"{base_url.rstrip('/')}{VIEWER_PATH}"
