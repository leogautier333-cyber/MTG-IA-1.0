import json
import os
from datetime import datetime, timezone
from pathlib import Path

import scryfall
from modules.market_analyzer import detect_watchlist_drops


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
STATE_FILE = DATA_DIR / "nexus_market_state.json"


def _read_state():
    try:
        with STATE_FILE.open("r", encoding="utf-8") as state_file:
            state = json.load(state_file)
        return state if isinstance(state, dict) else {"cards": {}}
    except (OSError, json.JSONDecodeError):
        return {"cards": {}}


def get_watchlist_observations():
    return _read_state().get("cards", {})


def _write_state(state):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary_file = STATE_FILE.with_suffix(".tmp")
    with temporary_file.open("w", encoding="utf-8") as state_file:
        json.dump(state, state_file, ensure_ascii=False, indent=2)
    os.replace(temporary_file, STATE_FILE)


def refresh_watchlist_prices(watchlist, collection):
    watchlist_by_id = {
        card.get("scryfall_id"): card for card in watchlist
        if card.get("scryfall_id")
    }
    owned_ids = {card.get("scryfall_id") for card in collection}
    monitored_ids = [card_id for card_id in watchlist_by_id if card_id not in owned_ids]
    if not monitored_ids:
        return {"observed": 0, "alerts": [], "message": "Aucune carte hors-stock de la watchlist avec un identifiant Scryfall."}

    current_cards = scryfall.get_cards_batch(monitored_ids)
    if not current_cards:
        return {"observed": 0, "alerts": [], "message": "Scryfall n'a renvoyé aucun prix; vérifie la connexion puis réessaie."}

    state = _read_state()
    tracked = state.setdefault("cards", {})
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    observed_count = 0
    for current_card in current_cards:
        card_id = current_card.get("id")
        watched_card = watchlist_by_id.get(card_id)
        if not watched_card:
            continue
        prices = current_card.get("prices") or {}
        price_key = "eur_foil" if watched_card.get("foil") else "eur"
        raw_price = prices.get(price_key) or (prices.get("eur") if price_key == "eur_foil" else None)
        if raw_price is None:
            continue
        observations = tracked.setdefault(card_id, [])
        observations.append({"observed_at": now, "price_eur": float(raw_price)})
        del observations[:-100]
        observed_count += 1

    _write_state(state)
    alerts = detect_watchlist_drops(watchlist, collection, tracked)
    return {"observed": observed_count, "alerts": alerts, "message": "Prix consultés via Scryfall; il s'agit de snapshots, pas d'un flux temps réel."}