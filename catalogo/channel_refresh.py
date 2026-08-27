import copy
import os
import re
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone

from django.conf import settings
from django.core.cache import cache
from django.db import close_old_connections


_LOCK = threading.Lock()
_STATE = {
    "run_id": None,
    "status": "IDLE",
    "started_at": None,
    "finished_at": None,
    "current_channel": None,
    "channels": [],
    "external_writes": 0,
}

LIVE_STAGES = [
    ("SHOPIFY", "Shopify", ("refresh_shopify_snapshot", "--full"), 1000),
    ("SIIGO", "Siigo", ("refresh_siigo_snapshot",), 1000),
    ("MERCADO_LIBRE", "Mercado Libre", ("refresh_mercadolibre_snapshot",), 500),
    ("FALABELLA", "Falabella", ("refresh_falabella_snapshot",), 700),
]
MANUAL_STAGES = [
    ("SODIMAC", "Sodimac / Homecenter", "Se conserva el último archivo importado; no existe lector API en vivo."),
    ("MADECENTRO", "Madecentro", "Se conserva el piloto importado; no existe lector API en vivo."),
    ("RAPPI", "Rappi", "Canal todavía sin conector de catálogo."),
]


def _now():
    return datetime.now(timezone.utc).isoformat()


def _safe_message(value):
    text = str(value or "").strip()
    text = re.sub(r"(?i)(token|secret|key|password|authorization)\s*[=:]\s*\S+", r"\1=[oculto]", text)
    return text[-1200:]


def get_channel_refresh_state():
    with _LOCK:
        return copy.deepcopy(_STATE)


def _replace_state(**values):
    with _LOCK:
        _STATE.update(values)


def _update_channel(code, **values):
    with _LOCK:
        for row in _STATE["channels"]:
            if row["code"] == code:
                row.update(values)
                break


def _run_command(command_parts, timeout):
    environment = os.environ.copy()
    environment["RAILWAY_CALLER"] = "skill:use-railway@1.3.7"
    environment["RAILWAY_AGENT_SESSION"] = "railway-skill-catalog-refresh-20260826"
    completed = subprocess.run(
        [sys.executable, "manage.py", *command_parts],
        cwd=settings.BASE_DIR,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        env=environment,
    )
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if completed.returncode != 0:
        error_lines = [line.strip() for line in completed.stderr.splitlines() if line.strip()]
        raise RuntimeError(_safe_message(" | ".join(error_lines[-5:]) or "La fuente rechazó la lectura."))
    return _safe_message(lines[-1] if lines else "Lectura completada; externalWrites=0.")


def _worker(run_id):
    close_old_connections()
    failures = 0
    try:
        for code, _label, command_name, timeout in LIVE_STAGES:
            _replace_state(current_channel=code)
            _update_channel(code, status="RUNNING", started_at=_now(), message="Leyendo la fuente sin modificarla…")
            try:
                message = _run_command(command_name, timeout)
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                failures += 1
                message = "La fuente no pudo actualizarse; se conserva el último snapshot correcto."
                detail = _safe_message(error)
                if detail:
                    message = f"{message} {detail}"
                _update_channel(code, status="FAILED", finished_at=_now(), message=message)
            else:
                cache.clear()
                _update_channel(code, status="SUCCEEDED", finished_at=_now(), message=message)
        _replace_state(
            status="PARTIAL" if failures else "SUCCEEDED",
            current_channel=None,
            finished_at=_now(),
        )
    except Exception as error:
        _replace_state(
            status="FAILED", current_channel=None, finished_at=_now(),
            error=_safe_message(error),
        )
    finally:
        close_old_connections()


def start_channel_refresh():
    with _LOCK:
        if _STATE["status"] == "RUNNING":
            return copy.deepcopy(_STATE), False
        run_id = str(uuid.uuid4())
        channels = [
            {"code": code, "label": label, "status": "PENDING", "message": "En espera."}
            for code, label, _command, _timeout in LIVE_STAGES
        ] + [
            {"code": code, "label": label, "status": "MANUAL_SOURCE", "message": message}
            for code, label, message in MANUAL_STAGES
        ]
        _STATE.clear()
        _STATE.update({
            "run_id": run_id,
            "status": "RUNNING",
            "started_at": _now(),
            "finished_at": None,
            "current_channel": None,
            "channels": channels,
            "external_writes": 0,
        })
        snapshot = copy.deepcopy(_STATE)
    threading.Thread(target=_worker, args=(run_id,), daemon=True, name=f"catalog-refresh-{run_id[:8]}").start()
    return snapshot, True
