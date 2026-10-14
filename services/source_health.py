import os
from datetime import datetime, timedelta, timezone

import data_manager


DEFAULT_MAX_AGE_HOURS = {
    "scryfall": max(1, int(os.getenv("NEXUS_SCRYFALL_MAX_AGE_HOURS", "36"))),
    "scryfall_bulk": max(1, int(os.getenv("NEXUS_SCRYFALL_BULK_MAX_AGE_HOURS", "36"))),
    "reddit": max(1, int(os.getenv("NEXUS_REDDIT_MAX_AGE_HOURS", "48"))),
}


def get_source_statuses(now=None):
    now = now or datetime.now(timezone.utc)
    recorded = data_manager.storage.get_source_health()
    statuses = {}
    for source, max_age in DEFAULT_MAX_AGE_HOURS.items():
        check = recorded.get(source)
        if not check:
            statuses[source] = {"status": "UNKNOWN", "details": "Aucun contrôle enregistré."}
            continue
        try:
            checked_at = datetime.fromisoformat(check["checked_at"])
            if checked_at.tzinfo is None:
                checked_at = checked_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            statuses[source] = {"status": "UNKNOWN", "details": "Horodatage de contrôle invalide."}
            continue
        if not check["succeeded"]:
            status = "FAILED"
        elif now - checked_at > timedelta(hours=max_age):
            status = "STALE"
        else:
            status = "HEALTHY"
        statuses[source] = {
            "status": status,
            "checked_at": checked_at.isoformat(timespec="seconds"),
            "details": check["details"],
        }
    return statuses
