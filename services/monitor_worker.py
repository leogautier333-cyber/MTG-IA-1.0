import json
import logging
import os
import time
from pathlib import Path

import requests

import data_manager
from services.market_monitor import refresh_watchlist_prices
from services.spoiler_watcher import scan_spoilers_and_trends


LOGGER = logging.getLogger("magic_nexus.monitor")
POLL_INTERVAL_SECONDS = max(300, int(os.getenv("NEXUS_POLL_INTERVAL_SECONDS", "3600")))
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
ALERT_STATE_FILE = Path(__file__).resolve().parents[1] / "data" / "nexus_alert_state.json"


def _read_sent_alerts():
    try:
        with ALERT_STATE_FILE.open("r", encoding="utf-8") as state_file:
            state = json.load(state_file)
        return set(state if isinstance(state, list) else [])
    except (OSError, json.JSONDecodeError):
        return set()


def _write_sent_alerts(sent_alerts):
    ALERT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = ALERT_STATE_FILE.with_suffix(".tmp")
    with temporary_file.open("w", encoding="utf-8") as state_file:
        json.dump(sorted(sent_alerts)[-2000:], state_file, ensure_ascii=False, indent=2)
    os.replace(temporary_file, ALERT_STATE_FILE)


def _notify_once(alert_key, title, description, sent_alerts):
    if alert_key in sent_alerts:
        return False
    if DISCORD_WEBHOOK_URL:
        response = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"embeds": [{"title": title, "description": description, "color": 16760576}]},
            timeout=10,
        )
        response.raise_for_status()
    else:
        LOGGER.warning("Notification non envoyée (webhook absent): %s | %s", title, description)
        return False
    sent_alerts.add(alert_key)
    return True


def run_once():
    collection = data_manager.get_collection()
    watchlist = data_manager.get_watchlist()
    sent_alerts = _read_sent_alerts()
    notifications_sent = 0

    if watchlist:
        market_result = refresh_watchlist_prices(watchlist, collection)
        for alert in market_result["alerts"]:
            alert_key = (
                f"price:{alert['name']}:{alert['previous_price']:.4f}:"
                f"{alert['current_price']:.4f}"
            )
            notifications_sent += _notify_once(
                alert_key,
                f"[OPPORTUNITÉ ACHAT] {alert['name']}",
                f"Baisse de {alert['change_pct']:.1f}% sur {alert['currency_source']} "
                f"({alert['previous_price']:.2f} € → {alert['current_price']:.2f} €).",
                sent_alerts,
            )

        intelligence_result = scan_spoilers_and_trends(watchlist, collection)
        for alert in intelligence_result["alerts"]:
            alert_key = alert["id"]
            description = alert["message"]
            if alert.get("source_url"):
                description = f"{description}\n{alert['source_url']}"
            notifications_sent += _notify_once(
                alert_key,
                f"Nexus · {alert['category']} · {alert['card_name']}",
                description,
                sent_alerts,
            )
        for error in intelligence_result["errors"]:
            LOGGER.warning("Source de veille indisponible: %s", error)

    _write_sent_alerts(sent_alerts)
    LOGGER.info("Cycle terminé; %d notification(s) envoyée(s).", notifications_sent)
    return notifications_sent


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    LOGGER.info("Nexus monitor lancé; intervalle=%d s.", POLL_INTERVAL_SECONDS)
    while True:
        try:
            run_once()
        except Exception:
            LOGGER.exception("Échec du cycle de veille.")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()