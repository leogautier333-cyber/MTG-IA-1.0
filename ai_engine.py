import json
import os
from datetime import datetime
from pathlib import Path

import requests

import data_manager
import scryfall


DATA_DIR = Path(__file__).resolve().parent / "data"
PREDICTIONS_FILE = DATA_DIR / "ai_predictions.json"


def _read_json(path, default):
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return default


def _write_json_atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary_path.open("w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _as_price(value):
    if value in (None, ""):
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if price >= 0 else None


def _prediction_id(category, scryfall_id, timestamp_key):
    return f"pred_{category.lower()}_{scryfall_id}_{timestamp_key}"


def _variant_history(card):
    foil = bool(card.get("foil"))
    price_key = "eur_foil" if foil else "eur"
    return {
        point["snapshot_date"]: {price_key: point["price_eur"]}
        for point in data_manager.storage.get_variant_price_history(
            card.get("scryfall_id"), foil, card.get("language", "FR"),
            card.get("condition", "NM"),
        )
    }


def _create_market_predictions(collection, watchlist, now):
    existing_predictions = _read_json(PREDICTIONS_FILE, [])
    if not isinstance(existing_predictions, list):
        existing_predictions = []
    existing_ids = {
        entry.get("prediction_id")
        for entry in existing_predictions
        if isinstance(entry, dict)
    }
    predictions = []

    for item in collection:
        if item.get("status") != "FOR_SALE":
            continue
        card_id = item.get("scryfall_id")
        if not card_id:
            continue
        history = _variant_history(item)
        dates = sorted(history)
        if len(dates) < 2:
            continue

        latest_date, previous_date = dates[-1], dates[-2]
        latest_point = history[latest_date]
        previous_point = history[previous_date]
        price_key = "eur_foil" if item.get("foil") else "eur"
        latest_price = _as_price(latest_point.get(price_key))
        previous_price = _as_price(previous_point.get(price_key))
        if latest_price is None or previous_price is None or previous_price <= 0:
            continue

        difference = latest_price - previous_price
        change_pct = difference / previous_price * 100
        if change_pct >= 15 and difference >= 1.0:
            category = "SELL_RECOMMENDATION"
            prediction_id = _prediction_id(category, card_id, latest_date)
            if prediction_id in existing_ids:
                continue
            predictions.append({
                "prediction_id": prediction_id,
                "timestamp": now.isoformat(),
                "scryfall_id": card_id,
                "card_name": item.get("name", "Carte inconnue"),
                "category": category,
                "suggested_action": "RAISE_PRICE",
                "confidence_score": 0.85,
                "target_price": round(latest_price * 1.1, 2),
                "reference_price": latest_price,
                "foil": bool(item.get("foil")),
                "language": item.get("language", "FR"),
                "condition": item.get("condition", "NM"),
                "ban_risk_level": "LOW",
                "reprint_risk_level": "LOW",
                "reasoning": (
                    f"Hausse rapide détectée (+{change_pct:.1f}% / "
                    f"+{difference:.2f} €). Réaligne ton prix vers le haut."
                ),
                "verified": False,
                "actual_outcome": None,
            })
            existing_ids.add(prediction_id)

    for item in watchlist:
        card_id = item.get("scryfall_id")
        if not card_id:
            continue
        if any(
            card.get("scryfall_id") == card_id and card.get("status") == "FOR_SALE"
            for card in collection
        ):
            continue

        history = _variant_history(item)
        dates = sorted(history)
        if len(dates) < 2:
            continue

        latest_date, previous_date = dates[-1], dates[-2]
        price_key = "eur_foil" if item.get("foil") else "eur"
        latest_price = _as_price(history[latest_date].get(price_key))
        previous_price = _as_price(history[previous_date].get(price_key))
        if latest_price is None or previous_price is None or previous_price <= 0:
            continue

        difference = latest_price - previous_price
        change_pct = difference / previous_price * 100
        if change_pct <= -15 and abs(difference) >= 1.0:
            category = "BUY_SIGNAL"
            prediction_id = _prediction_id(category, card_id, latest_date)
            if prediction_id in existing_ids:
                continue
            predictions.append({
                "prediction_id": prediction_id,
                "timestamp": now.isoformat(),
                "scryfall_id": card_id,
                "card_name": item.get("name", "Carte inconnue"),
                "category": category,
                "suggested_action": "CONSIDER_BUYING",
                "confidence_score": 0.75,
                "target_price": latest_price,
                "reference_price": latest_price,
                "foil": bool(item.get("foil")),
                "language": item.get("language", "FR"),
                "condition": item.get("condition", "NM"),
                "ban_risk_level": "UNKNOWN",
                "reprint_risk_level": "UNKNOWN",
                "reasoning": (
                    f"Baisse détectée sur la watchlist ({change_pct:.1f}% / "
                    f"{difference:.2f} €). Vérifie l'édition et le prix du marché avant achat."
                ),
                "verified": False,
                "actual_outcome": None,
            })
            existing_ids.add(prediction_id)

    return predictions


def analyze_market_and_predict():
    """Actualise les prix suivis, détecte les mouvements et persiste les alertes."""
    collection = data_manager.get_collection()
    watchlist = data_manager.get_watchlist()
    tracked_cards = {}
    for card in [*collection, *watchlist]:
        card_id = card.get("scryfall_id")
        if card_id:
            variant = (
                bool(card.get("foil")),
                str(card.get("language", "FR")),
                str(card.get("condition", "NM")),
            )
            tracked_cards.setdefault(card_id, set()).add(variant)

    timestamp = datetime.now()
    updated_count = 0
    card_ids = list(tracked_cards)
    if card_ids:
        try:
            updated_cards = scryfall.get_cards_batch(card_ids)
            data_manager.storage.record_source_health("scryfall", True)
        except requests.RequestException as error:
            print(f"Impossible d'actualiser les prix Scryfall : {error}")
            data_manager.storage.record_source_health("scryfall", False, str(error))
            updated_cards = []

        for card_data in updated_cards:
            card_id = card_data.get("id")
            if not card_id:
                continue
            prices = card_data.get("prices") or {}
            eur = _as_price(prices.get("eur"))
            eur_foil = _as_price(prices.get("eur_foil"))
            if eur is None and eur_foil is None:
                continue

            for foil, language, condition in tracked_cards.get(card_id, set()):
                price = eur_foil if foil else eur
                if price is None:
                    continue
                data_manager.record_price_snapshot(
                    card_id, price, foil=foil, language=language, condition=condition,
                    observed_at=timestamp.isoformat(timespec="seconds"),
                )
                updated_count += 1
    else:
        print("Aucune carte avec un identifiant Scryfall à analyser.")

    predictions = _create_market_predictions(
        collection,
        watchlist,
        timestamp,
    )
    if predictions:
        for prediction in predictions:
            data_manager.storage.record_alert(
                prediction["prediction_id"], prediction["category"],
                prediction["scryfall_id"], prediction["timestamp"], prediction,
            )
        saved_predictions = _read_json(PREDICTIONS_FILE, [])
        if not isinstance(saved_predictions, list):
            saved_predictions = []
        saved_predictions.extend(predictions)
        _write_json_atomic(PREDICTIONS_FILE, saved_predictions)

    print(
        f"Analyse terminée : {updated_count} prix actualisé(s), "
        f"{len(predictions)} nouvelle(s) alerte(s)."
    )
    return predictions


if __name__ == "__main__":
    analyze_market_and_predict()