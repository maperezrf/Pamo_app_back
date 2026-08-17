from django.utils import timezone

from ..models import GovernancePrototipo

RAMA_A_CAMPO_MERGE = {
    "main": "merged_to_produccion",
    "dev": "merged_to_desarrollo",
}


def procesar_webhook_github_merge(payload, github_event_id):
    """Actualiza los campos de merge de un `GovernancePrototipo` a partir de
    un evento `pull_request` de GitHub. `github_event_id` es el header
    `X-GitHub-Delivery` -- idempotente: reintentar el mismo delivery no
    vuelve a aplicar el cambio (ver GOVERNANCE.md §4.3.5). No hace nada
    (pero responde 200) si el evento no es un merge, si la rama no es
    `main`/`dev`, o si no hay un prototipo registrado para ese repositorio."""

    pull_request = payload.get("pull_request") or {}
    if not pull_request.get("merged"):
        return None

    repo_url = (payload.get("repository") or {}).get("html_url")
    if not repo_url:
        return None

    prototipo = GovernancePrototipo.objects.filter(url_github=repo_url).first()
    if prototipo is None:
        return None

    if github_event_id and github_event_id == prototipo.github_event_id:
        return prototipo

    rama_base = pull_request.get("base", {}).get("ref")
    campo_merge = RAMA_A_CAMPO_MERGE.get(rama_base)
    if campo_merge is None:
        return None

    setattr(prototipo, campo_merge, True)
    prototipo.url_merge_request = pull_request.get("html_url", prototipo.url_merge_request)
    prototipo.github_event_id = github_event_id
    prototipo.ultima_actualizacion_webhook = timezone.now()
    prototipo.save(update_fields=[
        campo_merge,
        "url_merge_request",
        "github_event_id",
        "ultima_actualizacion_webhook",
    ])
    return prototipo
