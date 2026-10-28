"""Logique métier du stock, indépendante de Streamlit ; persistance via database.storage."""
from datetime import datetime, timezone

from database import storage

REQUIRED_COLLECTION_FIELDS = {"scryfall_id", "name", "set_code", "purchase_price", "selling_price"}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_valid_collection(collection):
    return isinstance(collection, list) and all(
        isinstance(card, dict) and REQUIRED_COLLECTION_FIELDS.issubset(card)
        for card in collection
    )


def get_collection():
    return storage.get_collection()


def save_collection(collection):
    if not is_valid_collection(collection):
        raise ValueError("La collection ne respecte pas les champs requis.")
    storage.save_collection(collection)


def replace_collection(collection):
    if not is_valid_collection(collection):
        return False
    storage.save_collection(collection)
    return True


def add_card_to_collection(**kwargs):
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
        "date_added": _now(),
        "image_url": kwargs.get("image_url", ""),
        "mana_cost": kwargs.get("mana_cost", ""),
        "type_line": kwargs.get("type_line", ""),
        "oracle_text": kwargs.get("oracle_text", ""),
        "legalities": kwargs.get("legalities", {}),
    }
    storage.append_inventory_card(card)
    return True


def mark_card_as_sold(scryfall_id, actual_sale_price):
    return storage.mark_inventory_sold(scryfall_id, float(actual_sale_price), _now())


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


def get_watchlist():
    return storage.get_watchlist()


def save_watchlist(watchlist):
    storage.save_watchlist(watchlist)


def get_price_history():
    return storage.get_price_history()


def get_variant_price_history(scryfall_id, foil=False, language="FR", condition="NM"):
    return storage.get_variant_price_history(scryfall_id, foil, language, condition)


def record_price_snapshot(scryfall_id, price, foil=False, language="FR", condition="NM", observed_at=None):
    return storage.record_price_snapshot(
        scryfall_id, price, foil, language, condition, observed_at
    )


def get_price_alerts():
    return storage.get_price_alerts()


def save_price_alerts(alerts):
    storage.save_price_alerts(alerts)
