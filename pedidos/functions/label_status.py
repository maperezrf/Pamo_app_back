LABEL_AVAILABLE = "available"
LABEL_PENDING_PROVIDER = "pending_provider"
LABEL_NOT_PRINTABLE = "not_printable"
LABEL_TEMPORARY_ERROR = "temporary_error"

VALID_LABEL_STATUSES = {
    LABEL_AVAILABLE,
    LABEL_PENDING_PROVIDER,
    LABEL_NOT_PRINTABLE,
    LABEL_TEMPORARY_ERROR,
}


def _mapping(value):
    return value if isinstance(value, dict) else {}


def label_source_metadata(shipment_data, channel):
    source = _mapping(shipment_data.get("source_snapshot"))
    logistic = _mapping(source.get("logistic"))
    return {
        "provider": str(channel or "").strip().lower(),
        "status": str(source.get("status") or "").strip(),
        "substatus": str(source.get("substatus") or "").strip(),
        "logistic_mode": str(logistic.get("mode") or source.get("mode") or "").strip(),
        "logistic_type": str(
            logistic.get("type") or source.get("logistic_type") or ""
        ).strip(),
    }


def inferred_label_availability(shipment_data, channel):
    source = label_source_metadata(shipment_data, channel)
    documents = shipment_data.get("documents")
    has_remote_document = isinstance(documents, list) and bool(documents)
    tracking_number = str(shipment_data.get("tracking_number") or "").strip()

    if has_remote_document:
        return {
            "status": LABEL_PENDING_PROVIDER,
            "reason": "CANONICAL_DOCUMENT_READY_FOR_LOCAL_CACHE",
            "checked_at": None,
        }
    if source["provider"] == "mercado-libre" and source["logistic_type"] == "fulfillment":
        return {
            "status": LABEL_NOT_PRINTABLE,
            "reason": "MERCADOLIBRE_FULFILLMENT_NO_SELLER_LABEL",
            "checked_at": None,
        }
    if not tracking_number:
        return {
            "status": LABEL_PENDING_PROVIDER,
            "reason": "TRACKING_NOT_AVAILABLE",
            "checked_at": None,
        }
    return {
        "status": LABEL_PENDING_PROVIDER,
        "reason": "LABEL_FETCH_SCHEDULED",
        "checked_at": None,
    }


def set_label_availability(snapshot, status, reason, *, checked_at=None):
    updated = dict(snapshot if isinstance(snapshot, dict) else {})
    updated["label_availability"] = {
        "status": status if status in VALID_LABEL_STATUSES else LABEL_TEMPORARY_ERROR,
        "reason": str(reason or "LABEL_STATUS_UNKNOWN")[:120],
        "checked_at": (
            checked_at.isoformat()
            if hasattr(checked_at, "isoformat")
            else checked_at
        ),
    }
    return updated


def serialized_label_availability(shipment):
    if hasattr(shipment, "document"):
        return {
            "status": LABEL_AVAILABLE,
            "reason": "LOCAL_DOCUMENT_AVAILABLE",
            "checked_at": shipment.document.uploaded_at.isoformat(),
        }
    snapshot = _mapping(shipment.source_snapshot)
    availability = _mapping(snapshot.get("label_availability"))
    status = availability.get("status")
    if status not in VALID_LABEL_STATUSES:
        status = LABEL_PENDING_PROVIDER
    return {
        "status": status,
        "reason": str(availability.get("reason") or "LABEL_NOT_CACHED")[:120],
        "checked_at": availability.get("checked_at"),
    }
