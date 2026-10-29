import argparse
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone

import requests
import scryfall

from database import market_data, storage
from services import inventory, spoiler_watcher, stock_advisor


LOGGER = logging.getLogger("magic_nexus.phase_one")
ALERT_THRESHOLD_PCT = 10.0
POLL_INTERVAL_SECONDS = max(300, int(os.getenv("NEXUS_POLL_INTERVAL_SECONDS", "3600")))
DISCORD_WEBHOOK_URL = "DISCORD_WEBHOOK_URL"
PRICE_ALERT = "PRICE"
COLOR_UP, COLOR_DOWN, COLOR_INFO = 3066993, 15158332, 16760576


def _send_webhook(title, description, session=requests, webhook_url=None, fields=None, color=COLOR_INFO):
    webhook_url = webhook_url or os.getenv(DISCORD_WEBHOOK_URL)
    if not webhook_url:
        raise RuntimeError("DISCORD_WEBHOOK_URL n'est pas configurée.")
    embed = {"title": title, "description": description, "color": color}
    if fields:
        embed["fields"] = fields
    try:
        response = session.post(webhook_url, json={"embeds": [embed]}, timeout=10)
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
            "set_code": str(card.get("set_code", "")).upper(),
            "quantity": 0,
        })
        position["quantity"] += max(1, int(card.get("quantity") or 1))
        target = float(card.get("target_price") or 0)
        if target > 0:
            position["target_price"] = min(target, position.get("target_price") or target)
    return positions


def _create_target_alert(position, current_price, observed_at):
    target = position["target_price"]
    identity = f"target|{position['scryfall_id']}|{position['foil']}|{target:.2f}"
    return {
        "id": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "kind": "TARGET_REACHED",
        "status": "pending",
        "created_at": observed_at,
        "scryfall_id": position["scryfall_id"],
        "foil": position["foil"],
        "card_name": position["name"],
        "set_code": position.get("set_code", ""),
        "quantity": position["quantity"],
        "current_price": current_price,
        "target_price": target,
        "message": (
            f"{position['name']} : le prix du marché ({current_price:.2f} €) a atteint ton objectif "
            f"de vente de {target:.2f} €. Quantité en stock : {position['quantity']}."
        ),
        "verdict": "Objectif atteint : vente conseillée pour sécuriser la plus-value.",
    }


def _create_price_alert(position, previous, current, observed_at, advice=None, popularity_delta=None):
    previous_price = float(previous["price_eur"])
    current_price = float(current)
    change_pct = (current_price - previous_price) / previous_price * 100
    identity = "|".join((
        position["scryfall_id"], str(position["foil"]),
        previous["observed_at"], observed_at,
    ))
    alert_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    direction = "hausse" if change_pct > 0 else "baisse"
    advice = advice or {}
    return {
        "id": alert_id,
        "kind": PRICE_ALERT,
        "status": "pending",
        "created_at": observed_at,
        "scryfall_id": position["scryfall_id"],
        "foil": position["foil"],
        "card_name": position["name"],
        "set_code": position.get("set_code", ""),
        "quantity": position["quantity"],
        "previous_price": previous_price,
        "current_price": current_price,
        "change_pct": change_pct,
        "popularity_delta_pct": popularity_delta,
        "context": advice.get("context", ""),
        "verdict": advice.get("verdict", ""),
        "message": (
            f"{position['name']} ({'foil' if position['foil'] else 'non-foil'}) : "
            f"{direction} de {change_pct:+.1f} %, de {previous_price:.2f} € "
            f"à {current_price:.2f} € entre deux analyses. "
            f"Quantité en stock : {position['quantity']}."
        ),
    }


def _alert_embed(alert):
    """Construit le titre, la description, les champs et la couleur de l'embed Discord."""
    if alert.get("kind", PRICE_ALERT) != PRICE_ALERT:
        fields = []
        if alert.get("verdict"):
            fields.append({"name": "Verdict", "value": alert["verdict"], "inline": False})
        if alert.get("source_url"):
            fields.append({"name": "Source", "value": alert["source_url"], "inline": False})
        return (
            f"[STOCK · VEILLE] {alert['card_name']}", alert["message"], fields, COLOR_INFO,
        )
    change_pct = float(alert.get("change_pct", 0))
    fields = [
        {"name": "Carte", "value": f"{alert['card_name']}"
         + (" (foil)" if alert.get("foil") else ""), "inline": True},
        {"name": "Édition", "value": alert.get("set_code") or "n/d", "inline": True},
        {"name": "Quantité", "value": str(alert.get("quantity", "n/d")), "inline": True},
    ]
    if "previous_price" in alert and "current_price" in alert:
        fields.append({
            "name": "Prix",
            "value": f"{alert['previous_price']:.2f} € ➡️ {alert['current_price']:.2f} € ({change_pct:+.1f} %)",
            "inline": False,
        })
    fields.append({
        "name": "Popularité EDHREC",
        "value": stock_advisor.popularity_badge(alert.get("popularity_delta_pct")), "inline": True,
    })
    if alert.get("context"):
        fields.append({"name": "Contexte IA", "value": alert["context"], "inline": False})
    if alert.get("verdict"):
        fields.append({"name": "Verdict", "value": alert["verdict"], "inline": False})
    title_label = "HAUSSE" if change_pct > 0 else "BAISSE"
    return (
        f"[STOCK · {title_label}] {alert['card_name']}", alert["message"], fields,
        COLOR_UP if change_pct > 0 else COLOR_DOWN,
    )


def _deliver_pending_alerts(active_positions, session=requests):
    sent = 0
    changed = []
    active_ids = {scryfall_id for scryfall_id, _foil in active_positions}
    for alert in inventory.get_price_alerts():
        if alert.get("status") != "pending":
            continue
        if alert.get("kind", PRICE_ALERT) == PRICE_ALERT:
            still_in_stock = (alert.get("scryfall_id"), bool(alert.get("foil"))) in active_positions
        else:
            still_in_stock = alert.get("scryfall_id") in active_ids
        if not still_in_stock:
            alert["status"] = "cancelled"
            changed.append(alert)
            continue
        title, description, fields, color = _alert_embed(alert)
        try:
            _send_webhook(title, description, session=session, fields=fields, color=color)
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


def _notify_source_problem(errors, session=requests):
    """Prévient sur Discord quand Scryfall ou Reddit ne répondent pas (au plus une fois par jour)."""
    today = datetime.now(timezone.utc).date().isoformat()
    if storage.get_metadata("source_alert_date") == today:
        return
    try:
        _send_webhook(
            "[VEILLE · SOURCE INDISPONIBLE]",
            "Une source de données n'a pas répondu pendant l'analyse :\n- " + "\n- ".join(errors),
            session=session, color=COLOR_DOWN,
        )
    except RuntimeError as error:
        LOGGER.warning("Alerte de panne de source non envoyée : %s", error)
        return
    storage.set_metadata("source_alert_date", today)


def scan_stock_events(session=requests):
    """Veille spoilers/réimpressions/Reddit/bans limitée aux cartes du stock.

    Retourne les événements détectés (pour contextualiser les variations de prix) ; les
    nouveaux événements sont enregistrés comme alertes en attente de livraison Discord.
    """
    positions = _stock_positions(inventory.get_collection())
    if not positions:
        return []
    stock_cards = [
        {"name": p["name"], "scryfall_id": p["scryfall_id"], "set_code": p["set_code"].casefold(),
         "status": "FOR_SALE"}
        for p in positions.values()
    ]
    id_by_name = {card["name"].casefold(): card["scryfall_id"] for card in stock_cards}
    result = spoiler_watcher.scan_spoilers_and_trends([], stock_cards, session=session)
    for error in result.get("errors", []):
        LOGGER.warning("Veille marché partielle : %s", error)
    if result.get("errors"):
        _notify_source_problem(result["errors"], session=session)

    detected = []
    for item in (*result.get("alerts", []), *result.get("events", [])):
        category = item.get("category") or item.get("event_type")
        scryfall_id = item.get("scryfall_id") or id_by_name.get(str(item.get("card_name", "")).casefold())
        if not scryfall_id:
            continue
        detected.append({**item, "category": category, "scryfall_id": scryfall_id})

    known_ids = {alert.get("id") for alert in inventory.get_price_alerts()}
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new_alerts = []
    for item in detected:
        raw_id = item.get("id") or f"community:{item['category']}:{item['scryfall_id']}:{item.get('source_url')}"
        alert_id = hashlib.sha256(f"event|{raw_id}|{item['scryfall_id']}".encode("utf-8")).hexdigest()
        if alert_id in known_ids:
            continue
        known_ids.add(alert_id)
        message = item.get("message") or (
            f"{item.get('card_name')} : événement {item['category']} détecté "
            f"({item.get('evidence') or item.get('source') or 'Reddit'})."
        )
        new_alerts.append({
            "id": alert_id, "kind": item["category"], "status": "pending", "created_at": created_at,
            "scryfall_id": item["scryfall_id"], "foil": False,
            "card_name": item.get("card_name", "Carte inconnue"),
            "message": message, "source_url": item.get("source_url", ""),
            **stock_advisor.advise_event(item),
        })
    if new_alerts:
        inventory.save_price_alerts(new_alerts)
    return detected


def analyze_stock_prices(session=requests, events=()):
    positions = _stock_positions(inventory.get_collection())
    if not positions:
        notifications_sent = _deliver_pending_alerts(set(), session=session)
        return {
            "analyzed": 0, "snapshots_written": 0,
            "alerts_created": 0, "notifications_sent": notifications_sent,
        }

    events_by_card = {}
    for event in events:
        events_by_card.setdefault(event.get("scryfall_id"), []).append(event)
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
        market_data.record_demand_snapshot(position["scryfall_id"], card_data.get("edhrec_rank"))
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

        if position.get("target_price") and current_price >= position["target_price"]:
            target_alert = _create_target_alert(position, current_price, observed_at)
            if target_alert["id"] not in known_alert_ids:
                new_alerts.append(target_alert)
                known_alert_ids.add(target_alert["id"])

        if not previous or previous["price_eur"] <= 0:
            continue
        change_pct = (current_price - previous["price_eur"]) / previous["price_eur"] * 100
        if abs(change_pct) < ALERT_THRESHOLD_PCT:
            continue
        popularity_delta = stock_advisor.popularity_delta_pct(position["scryfall_id"])
        advice = stock_advisor.advise(
            change_pct, popularity_delta, events_by_card.get(position["scryfall_id"], ())
        )
        alert = _create_price_alert(
            position, previous, current_price, observed_at, advice, popularity_delta
        )
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


def run_analysis(session=requests):
    """Analyse complète : veille d'événements du stock puis variations de prix."""
    try:
        events = scan_stock_events(session=session)
    except Exception:
        LOGGER.exception("Échec de la veille spoilers/Reddit ; analyse des prix poursuivie.")
        events = []
    result = analyze_stock_prices(session=session, events=events)
    result["events_detected"] = len(events)
    return result


def run_forever():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    LOGGER.info("Veille du stock lancée; intervalle=%d s.", POLL_INTERVAL_SECONDS)
    while True:
        try:
            result = run_analysis()
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
        print(json.dumps(run_analysis(), ensure_ascii=False))
    else:
        run_forever()


if __name__ == "__main__":
    main()