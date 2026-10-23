import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock


DATA_DIR = Path(os.getenv("MTG_JSON_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
REQUIRED_COLLECTION_FIELDS = {"scryfall_id", "name", "set_code", "purchase_price", "selling_price"}
_WRITE_LOCK = RLock()


def _read_json(path, default):
    try:
        with path.open("r", encoding="utf-8") as source:
            return json.load(source)
    except FileNotFoundError:
        return default


def _write_json_atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    with _WRITE_LOCK:
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(value, temporary_file, ensure_ascii=False, indent=2)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path and temporary_path.exists():
                temporary_path.unlink()


def get_collection():
    collection = _read_json(DATA_DIR / "collection.json", [])
    if not is_valid_collection(collection):
        raise ValueError("collection.json doit contenir une liste de cartes valide.")
    return collection


def save_collection(collection):
    if not is_valid_collection(collection):
        raise ValueError("La collection ne respecte pas les champs requis.")
    _write_json_atomic(DATA_DIR / "collection.json", collection)


def replace_collection(collection):
    if not is_valid_collection(collection):
        return False
    save_collection(collection)
    return True


def is_valid_collection(collection):
    return isinstance(collection, list) and all(
        isinstance(card, dict) and REQUIRED_COLLECTION_FIELDS.issubset(card)
        for card in collection
    )


def add_card_to_collection(**kwargs):
    collection = get_collection()
    card = {
        "scryfall_id": kwargs.get("scryfall_id", ""),
        "name": kwargs.get("name", ""),
        "set_code": kwargs.get("set_code", ""),
        "collector_number": str(kwargs.get("collector_number", "")),
        "language": kwargs.get("language", "FR"),
        "condition": kwargs.get("condition", "NM"),
        "foil": bool(kwargs.get("foil", False)),
        "quantity": max(1, int(kwargs.get("quantity", 1))),
        "purchase_price": float(kwargs.get("purchase_price", 0) or 0),
        "selling_price": float(kwargs.get("selling_price", 0) or 0),
        "liquidity_rating": kwargs.get("liquidity_rating", "MEDIUM"),
        "status": "FOR_SALE",
        "date_added": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "image_url": kwargs.get("image_url", ""),
        "mana_cost": kwargs.get("mana_cost", ""),
        "type_line": kwargs.get("type_line", ""),
        "oracle_text": kwargs.get("oracle_text", ""),
        "legalities": kwargs.get("legalities", {}),
    }
    collection.append(card)
    save_collection(collection)
    return True


def mark_card_as_sold(scryfall_id, actual_sale_price):
    collection = get_collection()
    for card in collection:
        if card.get("scryfall_id") == scryfall_id and card.get("status", "FOR_SALE") == "FOR_SALE":
            card["status"] = "SOLD"
            card["actual_sale_price"] = float(actual_sale_price)
            card["date_sold"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            save_collection(collection)
            return True
    return False


def calculate_net_margin(selling_price, purchase_price, shipping=0.0, cm_fee_pct=0.05):
    selling_price = float(selling_price or 0)
    purchase_price = float(purchase_price or 0)
    shipping = float(shipping or 0)
    fee = selling_price * float(cm_fee_pct)
    net_profit = selling_price - fee - shipping - purchase_price
    return {
        "net_profit": round(net_profit, 2),
        "roi_percent": round(net_profit / purchase_price * 100, 1) if purchase_price > 0 else 0.0,
        "cm_fee": round(fee, 2),
    }


def get_price_history():
    history = _read_json(DATA_DIR / "price_history.json", {})
    if not isinstance(history, dict):
        raise ValueError("price_history.json doit contenir un objet JSON.")
    return history


def _parse_snapshot_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        text = str(value)
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d_%Hh")
        except ValueError:
            parsed = datetime.strptime(text, "%Y-%m-%d")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def get_variant_price_history(scryfall_id, foil=False, language="FR", condition="NM"):
    price_key = "eur_foil" if foil else "eur"
    card_history = get_price_history().get(str(scryfall_id), {})
    points = []
    for timestamp, prices in card_history.items():
        if not isinstance(prices, dict) or prices.get(price_key) in (None, ""):
            continue
        try:
            observed_at = _parse_snapshot_time(timestamp)
            price = float(prices[price_key])
        except (TypeError, ValueError):
            continue
        points.append({
            "observed_at": observed_at.isoformat(timespec="microseconds"),
            "snapshot_date": observed_at.date().isoformat(),
            "price_eur": price,
        })
    return sorted(points, key=lambda point: _parse_snapshot_time(point["observed_at"]))


def record_price_snapshot(scryfall_id, price, foil=False, language="FR", condition="NM", observed_at=None):
    if price in (None, ""):
        return False
    timestamp = observed_at or datetime.now(timezone.utc).isoformat(timespec="microseconds")
    parsed_timestamp = _parse_snapshot_time(timestamp)
    timestamp = parsed_timestamp.isoformat(timespec="microseconds")
    history = get_price_history()
    card_history = history.setdefault(str(scryfall_id), {})
    snapshot = dict(card_history.get(timestamp, {}))
    snapshot["eur_foil" if foil else "eur"] = float(price)
    card_history[timestamp] = snapshot
    _write_json_atomic(DATA_DIR / "price_history.json", history)
    return True