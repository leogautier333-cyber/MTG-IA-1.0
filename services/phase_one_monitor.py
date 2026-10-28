import argparse
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone

import requests
import scryfall

from services import inventory


LOGGER = logging.getLogger("magic_nexus.phase_one")
ALERT_THRESHOLD_PCT = 10.0
POLL_INTERVAL_SECONDS = max(300, int(os.getenv("NEXUS_POLL_INTERVAL_SECONDS", "3600")))
DISCORD_WEBHOOK_URL = "DISCORD_WEBHOOK_URL"


def _send_webhook(title, description, session=requests, webhook_url=None):
    webhook_url = webhook_url or os.getenv(DISCORD_WEBHOOK_URL)
    if not webhook_url:
        raise RuntimeError("DISCORD_WEBHOOK_URL n'est pas configurée.")
    try:
        response = session.post(
            webhook_url,
            json={"embeds": [{"title": title, "description": description, "color": 16760576}]},
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        raise RuntimeError(
            f"Échec de l'envoi Discord ({type(error).__name__}); l'URL du webhook est masquée."
        ) from None


def send_test_alert(session=requests, webhook_url=None):
    _send_webhook(
        "Test · Magic Trading & Profit Manager",
        "Alerte test envoyée manuellement. Le canal Discord reçoit bien les notifications de l'application.",
        session=session,
        webhook_url=webhook_url,
    )
    return True


def _stock_positions(collection):
    positions = {}
    for card in collection:
        if card.get("status", "FOR_SALE") != "FOR_SALE":
            continue
        scryfall_id = card.get("scryfall_id")
        if not scryfall_id:
            continue
        key = (str(scryfall_id), bool(card.get("foil")))
        position = positions.setdefault(key, {
            "scryfall_id": str(scryfall_id),
            "foil": bool(card.get("foil")),
            "name": card.get("name", "Carte inconnue"),
            "quantity": 0,
        })
        position["quantity"] += max(1, int(card.get("quantity") or 1))
    return positions


def _create_price_alert(position, previous, current, observed_at):
    previous_price = float(previous["price_eur"])
    current_price = float(current)
    change_pct = (current_price - previous_price) / previous_price * 100
    identity = "|".join((
        position["scryfall_id"], str(position["foil"]),
        previous["observed_at"], observed_at,
    ))
    alert_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    direction = "hausse" if change_pct > 0 else "baisse"
    return {
        "id": alert_id,
        "status": "pending",
        "created_at": observed_at,
        "scryfall_id": position["scryfall_id"],
        "foil": position["foil"],
        "card_name": position["name"],
        "previous_price": previous_price,
        "current_price": current_price,
        "change_pct": change_pct,
        "message": (
            f"{position['name']} ({'foil' if position['foil'] else 'non-foil'}) : "
            f"{direction} de {change_pct:+.1f} %, de {previous_price:.2f} € "
            f"à {current_price:.2f} € entre deux analyses. "
            f"Quantité en stock : {position['quantity']}."
        ),
    }


def _deliver_pending_alerts(active_positions, session=requests):
    sent = 0
    changed = []
    for alert in inventory.get_price_alerts():
        if alert.get("status") != "pending":
            continue
        position_key = (alert.get("scryfall_id"), bool(alert.get("foil")))
        if position_key not in active_positions:
            alert["status"] = "cancelled"
            changed.append(alert)
            continue
        try:
            _send_webhook(
                f"[STOCK · VARIATION DE PRIX] {alert['card_name']}",
                alert["message"],
                session=session,
            )
        except RuntimeError as error:
            LOGGER.warning("Alerte Discord conservée en attente (%s): %s", alert["id"], error)
            continue
        alert["status"] = "sent"
        alert["sent_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        changed.append(alert)
        sent += 1
    if changed:
        inventory.save_price_alerts(changed)
    return sent


def analyze_stock_prices(session=requests):
    positions = _stock_positions(inventory.get_collection())
    if not positions:
        notifications_sent = _deliver_pending_alerts(set(), session=session)
        return {
            "analyzed": 0, "snapshots_written": 0,
            "alerts_created": 0, "notifications_sent": notifications_sent,
        }

    card_ids = list(dict.fromkeys(position["scryfall_id"] for position in positions.values()))
    cards_by_id = {
        card.get("id"): card
        for card in scryfall.get_cards_batch(card_ids)
        if card.get("id")
    }
    observed_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    known_alert_ids = {alert.get("id") for alert in inventory.get_price_alerts()}
    new_alerts = []
    snapshots_written = 0

    for position in positions.values():
        card_data = cards_by_id.get(position["scryfall_id"])
        if not card_data:
            continue
        price_key = "eur_foil" if position["foil"] else "eur"
        raw_price = (card_data.get("prices") or {}).get(price_key)
        if raw_price in (None, ""):
            continue
        current_price = float(raw_price)
        history = inventory.get_variant_price_history(
            position["scryfall_id"], foil=position["foil"]
        )
        previous = history[-1] if history else None
        inventory.record_price_snapshot(
            position["scryfall_id"], current_price, foil=position["foil"],
            observed_at=observed_at,
        )
        snapshots_written += 1

        if not previous or previous["price_eur"] <= 0:
            continue
        change_pct = (current_price - previous["price_eur"]) / previous["price_eur"] * 100
        if abs(change_pct) < ALERT_THRESHOLD_PCT:
            continue
        alert = _create_price_alert(position, previous, current_price, observed_at)
        if alert["id"] not in known_alert_ids:
            new_alerts.append(alert)
            known_alert_ids.add(alert["id"])

    if new_alerts:
        inventory.save_price_alerts(new_alerts)
    notifications_sent = _deliver_pending_alerts(set(positions), session=session)
    return {
        "analyzed": len(positions),
        "snapshots_written": snapshots_written,
        "alerts_created": len(new_alerts),
        "notifications_sent": notifications_sent,
    }


def run_forever():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    LOGGER.info("Veille du stock lancée; intervalle=%d s.", POLL_INTERVAL_SECONDS)
    while True:
        try:
            result = analyze_stock_prices()
            LOGGER.info("Analyse stock: %s", result)
        except Exception:
            LOGGER.exception("Échec de l'analyse des prix du stock.")
        time.sleep(POLL_INTERVAL_SECONDS)


def main():
    parser = argparse.ArgumentParser(description="Veille des prix du stock Magic Trading.")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--once", action="store_true", help="Analyser le stock une fois.")
    action.add_argument("--test-discord", action="store_true", help="Envoyer une alerte test sans analyser le stock.")
    arguments = parser.parse_args()

    if arguments.test_discord:
        send_test_alert()
        print("Alerte test envoyée à Discord.")
    elif arguments.once:
        print(json.dumps(analyze_stock_prices(), ensure_ascii=False))
    else:
        run_forever()


if __name__ == "__main__":
    main()