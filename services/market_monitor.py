from datetime import datetime, timezone
import hashlib

import scryfall
import data_manager
from modules.market_analyzer import detect_watchlist_drops


def _variant_key(card):
    return (
        card.get("scryfall_id"), bool(card.get("foil")),
        str(card.get("language", "FR")), str(card.get("condition", "NM")),
    )


def get_watchlist_observations(watchlist=None):
    watchlist = watchlist if watchlist is not None else data_manager.get_watchlist()
    return {
        _variant_key(card): data_manager.storage.get_variant_price_history(
            card.get("scryfall_id"), card.get("foil", False),
            card.get("language", "FR"), card.get("condition", "NM"),
        )
        for card in watchlist if card.get("scryfall_id")
    }


def refresh_watchlist_prices(watchlist, collection):
    watchlist_by_id = {}
    for card in watchlist:
        if card.get("scryfall_id"):
            watchlist_by_id.setdefault(card["scryfall_id"], []).append(card)
    owned_ids = {card.get("scryfall_id") for card in collection}
    monitored_ids = [card_id for card_id in watchlist_by_id if card_id not in owned_ids]
    if not monitored_ids:
        return {"observed": 0, "alerts": [], "message": "Aucune carte hors-stock de la watchlist avec un identifiant Scryfall."}

    try:
        current_cards = scryfall.get_cards_batch(monitored_ids)
    except Exception as error:
        data_manager.storage.record_source_health("scryfall", False, str(error))
        raise
    if not current_cards:
        data_manager.storage.record_source_health(
            "scryfall", False, "Aucun prix retourné pour les impressions suivies."
        )
        return {"observed": 0, "alerts": [], "message": "Scryfall n'a renvoyé aucun prix; vérifie la connexion puis réessaie."}

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    observed_count = 0
    for current_card in current_cards:
        card_id = current_card.get("id")
        variants = watchlist_by_id.get(card_id)
        if not variants:
            continue
        prices = current_card.get("prices") or {}
        for watched_card in variants:
            foil = bool(watched_card.get("foil"))
            raw_price = prices.get("eur_foil" if foil else "eur")
            if raw_price is None:
                continue
            data_manager.record_price_snapshot(
                card_id, raw_price, foil=foil,
                language=watched_card.get("language", "FR"),
                condition=watched_card.get("condition", "NM"),
                observed_at=now,
            )
            observed_count += 1

    data_manager.storage.record_source_health("scryfall", True, "Prix watchlist actualisés.")
    tracked = get_watchlist_observations(watchlist)
    alerts = detect_watchlist_drops(watchlist, collection, tracked)
    for alert in alerts:
        watched_card = next(
            card for card in watchlist
            if _variant_key(card) == (
                alert["scryfall_id"], alert["foil"], alert["language"], alert["condition"]
            )
        )
        alert_id = hashlib.sha256(
            f"price-drop:{_variant_key(watched_card)}:{now[:10]}".encode("utf-8")
        ).hexdigest()
        storage_payload = {
            **alert,
            "reference_price": alert["current_price"],
            "foil": bool(watched_card.get("foil")),
            "language": watched_card.get("language", "FR"),
            "condition": watched_card.get("condition", "NM"),
        }
        data_manager.storage.record_alert(
            alert_id, "PRICE_DROP", watched_card.get("scryfall_id"), now,
            storage_payload,
        )
    return {"observed": observed_count, "alerts": alerts, "message": "Prix consultés via Scryfall; il s'agit de snapshots, pas d'un flux temps réel."}