"""Exposition et valeur estimée du stock actuellement en vente."""
from datetime import date

from services.pricing import CM_FEE_PCT, latest_market_price

STALE_AFTER_DAYS = 7
SCENARIO_DROPS = (10, 20, 30)


def _latest_snapshot_day(price_history, scryfall_id, foil):
    key = "eur_foil" if foil else "eur"
    days = price_history.get(str(scryfall_id)) or {}
    return max((day for day, point in days.items() if point.get(key)), default=None)


def summarize(collection, price_history, today=None):
    """Retourne des métriques nettes, la couverture des prix, l'exposition et des stress tests."""
    today = today or date.today()
    cards = [
        card for card in collection
        if card.get("status", "FOR_SALE") == "FOR_SALE"
    ]
    invested = priced_cost = gross_value = asking_value = 0.0
    known_cost_priced_value = 0.0
    copies = 0
    missing_prices = stale_prices = priced_cards = unknown_cost = 0
    positions_by_key = {}

    for card in cards:
        quantity = max(1, int(card.get("quantity") or 1))
        copies += quantity
        asking_value += float(card.get("selling_price") or 0) * quantity
        unit_cost = float(card.get("purchase_price") or 0)
        cost = unit_cost * quantity
        cost_known = not card.get("purchase_price_unknown", False)
        if cost_known:
            invested += cost
        else:
            unknown_cost += 1
        market = latest_market_price(
            price_history, card.get("scryfall_id"), bool(card.get("foil"))
        ) if card.get("scryfall_id") else None
        if market is None:
            missing_prices += 1
            continue

        value = market * quantity
        gross_value += value
        if cost_known:
            priced_cost += cost
            known_cost_priced_value += value
        priced_cards += 1
        snapshot_day = _latest_snapshot_day(
            price_history, card["scryfall_id"], bool(card.get("foil"))
        )
        try:
            age_days = (today - date.fromisoformat(snapshot_day[:10])).days
        except (TypeError, ValueError):
            age_days = None
        if age_days is None or age_days > STALE_AFTER_DAYS:
            stale_prices += 1
        key = (str(card.get("scryfall_id", "")), bool(card.get("foil")))
        position = positions_by_key.setdefault(key, {
            "name": card.get("name", "Carte inconnue"),
            "set_code": str(card.get("set_code", "")).upper(),
            "quantity": 0,
            "market_value": 0.0,
            "snapshot_day": snapshot_day,
        })
        position["quantity"] += quantity
        position["market_value"] += value
        position["snapshot_day"] = max(position["snapshot_day"], snapshot_day)

    net_value = gross_value * (1 - CM_FEE_PCT)
    positions = list(positions_by_key.values())
    for position in positions:
        position["share_pct"] = position["market_value"] / gross_value * 100 if gross_value else 0
    positions.sort(key=lambda position: position["market_value"], reverse=True)

    set_exposure = {}
    for position in positions:
        set_exposure[position["set_code"] or "Sans édition"] = (
            set_exposure.get(position["set_code"] or "Sans édition", 0)
            + position["market_value"]
        )

    return {
        "cards": len(cards),
        "copies": copies,
        "asking_value": round(asking_value, 2),
        "invested": round(invested, 2),
        "priced_cost": round(priced_cost, 2),
        "gross_value": round(gross_value, 2),
        "net_value": round(net_value, 2),
        "unrealized_net": round(known_cost_priced_value * (1 - CM_FEE_PCT) - priced_cost, 2),
        "roi_pct": round((known_cost_priced_value * (1 - CM_FEE_PCT) - priced_cost) / priced_cost * 100, 1) if priced_cost else None,
        "unknown_cost": unknown_cost,
        "missing_prices": missing_prices,
        "stale_prices": stale_prices,
        "coverage_pct": round(priced_cards / len(cards) * 100, 1) if cards else 0.0,
        "positions": positions,
        "set_exposure": sorted(set_exposure.items(), key=lambda item: item[1], reverse=True),
        "scenarios": [
            {
                "drop_pct": drop,
                "value_after_fees": round(gross_value * (1 - drop / 100) * (1 - CM_FEE_PCT), 2),
                "change_vs_now": round(-net_value * drop / 100, 2),
                "profit_vs_cost": round(
                    known_cost_priced_value * (1 - drop / 100) * (1 - CM_FEE_PCT) - priced_cost, 2
                ),
            }
            for drop in SCENARIO_DROPS
        ],
    }
