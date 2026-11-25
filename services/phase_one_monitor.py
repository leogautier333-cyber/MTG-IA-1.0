import argparse
import hashlib
import json
import logging
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal

import requests
import scryfall

from database import market_data, storage
from services import inventory, spoiler_watcher, stock_advisor
from services.money import decimal_amount


LOGGER = logging.getLogger("magic_nexus.phase_one")
ALERT_THRESHOLD_PCT = 10.0
# Valeur unitaire (prix de marché Scryfall, €) sous laquelle une variation est du bruit bulk.
MIN_ALERT_UNIT_PRICE = Decimal("1.00")
POLL_INTERVAL_SECONDS = max(300, int(os.getenv("NEXUS_POLL_INTERVAL_SECONDS", "3600")))
DISCORD_WEBHOOK_URL = "DISCORD_WEBHOOK_URL"
PRICE_ALERT = "PRICE"
COLOR_UP, COLOR_DOWN, COLOR_INFO = 3066993, 15158332, 16760576
DISCORD_SUMMARY_LIMIT = 3800
DISCORD_ATTACHMENT_LIMIT = 8 * 1024 * 1024
ANALYSIS_LOCK_ID = 734526419


@contextmanager
def _analysis_lock():
    if storage.get_database_backend() == "postgres":
        connection = storage.open_connection()
        try:
            acquired = connection.execute(
                "SELECT pg_try_advisory_xact_lock(?) AS acquired", (ANALYSIS_LOCK_ID,)
            ).fetchone()["acquired"]
            if not acquired:
                raise RuntimeError("Une analyse du stock est déjà en cours ; réessaie plus tard.")
            yield
        finally:
            connection.close()
    else:
        lock_path = storage.DATABASE_PATH.with_name(storage.DATABASE_PATH.name + ".analysis-lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(lock_path, timeout=0)
        try:
            try:
                connection.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as error:
                if getattr(error, "sqlite_errorcode", None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                    raise RuntimeError("Une analyse du stock est déjà en cours ; réessaie plus tard.") from error
                raise
            yield
        finally:
            connection.close()


def _price_trend(previous, current):
    """Compare les prix décimaux sans erreur d'arrondi aux seuils de 10 %."""
    previous_value, current_value = Decimal(str(previous)), Decimal(str(current))
    threshold = Decimal(str(ALERT_THRESHOLD_PCT))
    if current_value * 100 >= previous_value * (100 + threshold):
        return "UP"
    if current_value * 100 <= previous_value * (100 - threshold):
        return "DOWN"
    return "STABLE"


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
        "Test · Carnet de Mana",
        "Alerte test envoyée manuellement. Le canal Discord reçoit bien les notifications de l'application.",
        session=session,
        webhook_url=webhook_url,
    )
    return True


def meets_alert_floor(previous_price, current_price):
    """Vrai si la carte vaut au moins MIN_ALERT_UNIT_PRICE avant ou après la variation (Decimal)."""
    return max(decimal_amount(previous_price), decimal_amount(current_price)) >= MIN_ALERT_UNIT_PRICE


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
    trend = _price_trend(previous_price, current_price)
    if trend == "STABLE":
        identity = "|".join(("stable", position["scryfall_id"], str(position["foil"]), observed_at[:10]))
    else:
        identity = "|".join((
            position["scryfall_id"], str(position["foil"]),
            previous["observed_at"], observed_at,
        ))
    alert_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    direction = {"UP": "hausse", "DOWN": "baisse", "STABLE": "stagnation"}[trend]
    advice = advice or {}
    return {
        "id": alert_id,
        "kind": PRICE_ALERT,
        "trend": trend,
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
        fields.append({"name": "Contexte", "value": alert["context"], "inline": False})
    if alert.get("verdict"):
        fields.append({"name": "Verdict", "value": alert["verdict"], "inline": False})
    trend = alert.get("trend")
    title_label = {"UP": "HAUSSE", "DOWN": "BAISSE", "STABLE": "STAGNATION"}.get(trend)
    if not title_label:
        title_label = "HAUSSE" if change_pct > 0 else "BAISSE"
    color = {"HAUSSE": COLOR_UP, "BAISSE": COLOR_DOWN, "STAGNATION": COLOR_INFO}[title_label]
    return (
        f"[STOCK · {title_label}] {alert['card_name']}", alert["message"], fields,
        color,
    )


def _summary_group(alert):
    kind = alert.get("kind", PRICE_ALERT)
    if kind == PRICE_ALERT:
        trend = alert.get("trend")
        if trend not in {"UP", "DOWN", "STABLE"}:
            trend = "UP" if float(alert.get("change_pct", 0)) > 0 else "DOWN"
        return {"UP": "📈 Hausses", "DOWN": "📉 Baisses", "STABLE": "➡️ Stagnations"}[trend]
    return "🎯 Objectifs atteints" if kind == "TARGET_REACHED" else "📰 Veille du stock"


def _summary_line(alert):
    name = " ".join(str(alert.get("card_name") or "Carte inconnue").split())
    kind = alert.get("kind", PRICE_ALERT)
    if kind == PRICE_ALERT and "previous_price" in alert and "current_price" in alert:
        edition = str(alert.get("set_code") or "n/d").upper()
        foil = " · foil" if alert.get("foil") else ""
        quantity = alert.get("quantity", "n/d")
        line = (
            f"• {name} [{edition}{foil}] ×{quantity} : "
            f"{float(alert['previous_price']):.2f} € → {float(alert['current_price']):.2f} € "
            f"({float(alert.get('change_pct', 0)):+.1f} %)"
        )
        if alert.get("popularity_delta_pct") is not None:
            line += f" · EDHREC {float(alert['popularity_delta_pct']):+.1f} %"
    else:
        message = " ".join(str(alert.get("message") or name).split())
        line = f"• {message}"
    for key, label in (("context", "Contexte"), ("verdict", "Verdict"), ("source_url", "Source")):
        if alert.get(key):
            line += f"\n  {label} : {' '.join(str(alert[key]).split())}"
    return line


def _summary_payload(alerts, delivery_id=None, recovered=0, previous_ids=()):
    groups = {}
    for alert in alerts:
        groups.setdefault(_summary_group(alert), []).append(_summary_line(alert))
    order = ("📈 Hausses", "📉 Baisses", "➡️ Stagnations", "🎯 Objectifs atteints", "📰 Veille du stock")
    sections = [f"{group} ({len(groups[group])})\n" + "\n".join(groups[group])
                for group in order if group in groups]
    banner = ""
    if recovered:
        banner = (
            f"⚠️ Reprise après interruption : {recovered} alerte(s) ont peut-être déjà été reçues"
            + (f" (lot {', '.join(previous_ids)})" if previous_ids else "")
            + ". Ignore les doublons.\n\n"
        )
    report = "\n\n".join(sections)
    if len((banner + report).encode("utf-16-le")) // 2 <= DISCORD_SUMMARY_LIMIT:
        description = banner + report
        attachment = None
    else:
        counts = " · ".join(f"{group} : {len(groups[group])}" for group in order if group in groups)
        description = (
            f"{banner}{counts}\n\nLe détail complet est dans le fichier texte joint "
            "(une ligne par alerte)."
        )
        attachment = report.encode("utf-8")
        if len(attachment) > DISCORD_ATTACHMENT_LIMIT:
            raise RuntimeError("Le récapitulatif dépasse la taille maximale de la pièce jointe Discord.")
    embed = {"title": f"Récapitulatif du stock · {len(alerts)} alerte(s)",
             "description": description, "color": COLOR_INFO}
    if delivery_id:
        embed["footer"] = {"text": f"Lot {delivery_id}"}
    payload = {"embeds": [embed], "allowed_mentions": {"parse": []}}
    return payload, attachment


class DiscordDeliveryRejected(RuntimeError):
    """Discord a refusé le message ou la connexion n'a jamais abouti : rien n'a été publié."""


def _post_alert_summary(payload, attachment, session=requests):
    """Envoie le récapitulatif et retourne l'identifiant du message Discord s'il est fourni."""
    webhook_url = os.getenv(DISCORD_WEBHOOK_URL)
    if not webhook_url:
        raise DiscordDeliveryRejected("DISCORD_WEBHOOK_URL n'est pas configurée.")
    try:
        if attachment is None:
            response = session.post(webhook_url, json=payload, params={"wait": "true"}, timeout=10)
        else:
            response = session.post(
                webhook_url, params={"wait": "true"},
                data={"payload_json": json.dumps(payload, ensure_ascii=False)},
                files={"files[0]": ("alertes-stock.txt", attachment, "text/plain; charset=utf-8")},
                timeout=30,
            )
        response.raise_for_status()
    except (requests.HTTPError, requests.ConnectTimeout) as error:
        raise DiscordDeliveryRejected(
            f"Discord a refusé l'envoi ({type(error).__name__}); l'URL du webhook est masquée."
        ) from None
    except requests.RequestException as error:
        raise RuntimeError(
            f"Réponse Discord inconnue ({type(error).__name__}) : le message a peut-être été publié ; "
            "l'URL du webhook est masquée."
        ) from None
    try:
        message_id = response.json().get("id")
    except (ValueError, AttributeError):
        return None
    return message_id if isinstance(message_id, str) else None


def _deliver_pending_alerts(active_positions, session=requests):
    """Livre les alertes avec reprise explicite : `sending` signifie « envoi à confirmer ».

    Un envoi sans réponse exploitable laisse les alertes en `sending` ; la livraison suivante les
    renvoie avec un avertissement et l'identifiant du lot précédent, au lieu de les perdre ou de
    créer silencieusement un doublon. Un refus certain de Discord les remet en attente."""
    changed = []
    pending = []
    active_ids = {scryfall_id for scryfall_id, _foil in active_positions}
    for alert in inventory.get_price_alerts():
        if alert.get("status") not in ("pending", "sending"):
            continue
        if alert.get("kind", PRICE_ALERT) == PRICE_ALERT:
            still_in_stock = (alert.get("scryfall_id"), bool(alert.get("foil"))) in active_positions
        else:
            still_in_stock = alert.get("scryfall_id") in active_ids
        below_floor = (
            alert.get("kind", PRICE_ALERT) == PRICE_ALERT
            and "previous_price" in alert and "current_price" in alert
            and not meets_alert_floor(alert["previous_price"], alert["current_price"])
        )
        if not still_in_stock or below_floor:
            alert["status"] = "cancelled"
            changed.append(alert)
            continue
        pending.append(alert)
    sent = 0
    if pending:
        delivery_id = uuid.uuid4().hex[:8]
        unconfirmed = [alert for alert in pending if alert["status"] == "sending"]
        previous_ids = sorted({str(alert["delivery_id"]) for alert in unconfirmed if alert.get("delivery_id")})
        try:
            if not os.getenv(DISCORD_WEBHOOK_URL):
                raise RuntimeError("DISCORD_WEBHOOK_URL n'est pas configurée.")
            payload, attachment = _summary_payload(pending, delivery_id, len(unconfirmed), previous_ids)
        except RuntimeError as error:
            LOGGER.warning("%d alerte(s) Discord conservée(s) en attente : %s", len(pending), error)
        else:
            before = {alert["id"]: dict(alert) for alert in pending}
            claimed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for alert in pending:
                alert.update(
                    status="sending", delivery_id=delivery_id, delivery_started_at=claimed_at,
                    delivery_attempts=int(alert.get("delivery_attempts") or 0) + 1,
                )
            inventory.save_price_alerts([*changed, *pending])
            changed = []
            try:
                message_id = _post_alert_summary(payload, attachment, session=session)
            except DiscordDeliveryRejected as error:
                for alert in pending:
                    original = before[alert["id"]]
                    alert.clear()
                    alert.update(original)
                    alert["delivery_attempts"] = int(original.get("delivery_attempts") or 0) + 1
                changed = pending
                LOGGER.warning("%d alerte(s) Discord conservée(s) en attente : %s", len(pending), error)
            except RuntimeError as error:
                LOGGER.warning(
                    "%d alerte(s) Discord à confirmer (lot %s) : %s", len(pending), delivery_id, error
                )
            else:
                sent_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                for alert in pending:
                    alert.update(status="sent", sent_at=sent_at)
                    if message_id:
                        alert["discord_message_id"] = message_id
                changed = pending
                sent = len(pending)
    if changed:
        inventory.save_price_alerts(changed)
    return sent


def _notify_source_problem(errors, session=requests):
    """Prévient sur Discord quand Scryfall ne répond pas (au plus une fois par jour)."""
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
    """Veille des réimpressions annoncées par Scryfall, limitée aux cartes du stock.

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
    result = spoiler_watcher.scan_spoilers([], stock_cards, session=session)
    for error in result.get("errors", []):
        LOGGER.warning("Veille marché partielle : %s", error)
    if result.get("errors"):
        _notify_source_problem(result["errors"], session=session)

    detected = []
    for item in result.get("alerts", []):
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
            f"({item.get('evidence') or item.get('source') or 'Scryfall'})."
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
    with _analysis_lock():
        return _analyze_stock_prices(session=session, events=events)


def _analyze_stock_prices(session=requests, events=()):
    started = time.perf_counter()
    collection = inventory.get_collection()
    untracked = [
        card.get("name", "Carte inconnue") for card in collection
        if card.get("status", "FOR_SALE") == "FOR_SALE" and not card.get("scryfall_id")
    ]
    positions = _stock_positions(collection)
    if not positions:
        if untracked:
            LOGGER.warning(
                "Aucune position analysable : %d ligne(s) sans identifiant Scryfall.",
                len(untracked),
            )
        notifications_sent = _deliver_pending_alerts(set(), session=session)
        return {
            "analyzed": 0, "snapshots_written": 0,
            "alerts_created": 0, "notifications_sent": notifications_sent,
            "untracked": untracked, "missing_prices": [], "first_observations": [],
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
    fetched_at = time.perf_counter()
    observed_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    new_alerts = []
    missing_prices = []
    first_observations = []
    valid_positions = []
    ranks = {}
    for position in positions.values():
        card_data = cards_by_id.get(position["scryfall_id"])
        if not card_data:
            missing_prices.append(f"{position['name']} ({position['set_code']})")
            continue
        ranks[position["scryfall_id"]] = card_data.get("edhrec_rank")
        price_key = "eur_foil" if position["foil"] else "eur"
        raw_price = (card_data.get("prices") or {}).get(price_key)
        if raw_price in (None, ""):
            missing_prices.append(f"{position['name']} ({position['set_code']})")
            continue
        valid_positions.append((position, float(raw_price)))

    with storage.transaction() as connection:
        known_alert_ids = storage.get_price_alert_ids_with(connection)
        previous_prices = storage.get_latest_stock_snapshots_with(connection, card_ids)
        market_data.record_demand_snapshots_with(connection, ranks, observed_at[:10])
        demand_histories = market_data.get_edhrec_histories_with(connection, card_ids)
        for position, current_price in valid_positions:
            previous = previous_prices.get((position["scryfall_id"], position["foil"]))
            if position.get("target_price") and current_price >= position["target_price"]:
                target_alert = _create_target_alert(position, current_price, observed_at)
                if target_alert["id"] not in known_alert_ids:
                    new_alerts.append(target_alert)
                    known_alert_ids.add(target_alert["id"])

            if not previous or previous["price_eur"] <= 0:
                first_observations.append(f"{position['name']} ({position['set_code']})")
                continue
            if not meets_alert_floor(previous["price_eur"], current_price):
                continue
            change_pct = (current_price - previous["price_eur"]) / previous["price_eur"] * 100
            popularity_delta = stock_advisor.popularity_delta_from_history(
                demand_histories[position["scryfall_id"]]
            )
            if _price_trend(previous["price_eur"], current_price) == "STABLE":
                advice = {
                    "context": "Variation entre -10 % et +10 % depuis le relevé précédent.",
                    "verdict": "Prix relativement stable : continuer la surveillance.",
                }
            else:
                advice = stock_advisor.advise(
                    change_pct, popularity_delta, events_by_card.get(position["scryfall_id"], ())
                )
            alert = _create_price_alert(
                position, previous, current_price, observed_at, advice, popularity_delta
            )
            if alert["id"] not in known_alert_ids:
                new_alerts.append(alert)
                known_alert_ids.add(alert["id"])

        storage.record_stock_snapshots_with(
            connection,
            [(position["scryfall_id"], position["foil"], price)
             for position, price in valid_positions],
            observed_at,
        )
        storage.upsert_price_alerts_with(connection, new_alerts)

    persisted_at = time.perf_counter()
    notifications_sent = _deliver_pending_alerts(set(positions), session=session)
    LOGGER.info(
        "Analyse des prix : %d position(s), préparation/Scryfall %.2fs, SQL/calcul %.2fs, Discord %.2fs.",
        len(positions), fetched_at - started, persisted_at - fetched_at, time.perf_counter() - persisted_at,
    )
    if untracked or missing_prices or first_observations:
        LOGGER.warning(
            "Couverture des prix incomplète : %d ligne(s) sans identifiant Scryfall, "
            "%d position(s) sans prix, %d premier(s) relevé(s).",
            len(untracked), len(missing_prices), len(first_observations),
        )
    return {
        "analyzed": len(positions),
        "snapshots_written": len(valid_positions),
        "alerts_created": len(new_alerts),
        "notifications_sent": notifications_sent,
        "untracked": untracked,
        "missing_prices": missing_prices,
        "first_observations": first_observations,
    }


def run_analysis(session=requests):
    """Analyse complète : veille d'événements du stock puis variations de prix."""
    with _analysis_lock():
        started = time.perf_counter()
        try:
            events = scan_stock_events(session=session)
        except Exception:
            LOGGER.exception("Échec de la veille des réimpressions ; analyse des prix poursuivie.")
            events = []
        LOGGER.info("Veille événements : %.2fs.", time.perf_counter() - started)
        result = _analyze_stock_prices(session=session, events=events)
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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
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