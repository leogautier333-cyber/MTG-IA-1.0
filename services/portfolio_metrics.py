"""Exposition et valeur estimée du stock actuellement en vente."""
from datetime import date
from decimal import Decimal

from services.money import rounded_money
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
    invested = priced_cost = gross_value = asking_value = Decimal("0")
    known_cost_priced_value = Decimal("0")
    copies = 0
    missing_prices = stale_prices = priced_cards = unknown_cost = 0
    positions_by_key = {}

    for card in cards:
        quantity = max(1, int(card.get("quantity") or 1))
        copies += quantity
        asking_value += Decimal(str(card.get("selling_price") or 0)) * quantity
        unit_cost = Decimal(str(card.get("purchase_price") or 0))
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

        value = Decimal(str(market)) * quantity
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
            "market_value": Decimal("0"),
            "snapshot_day": snapshot_day,
        })
        position["quantity"] += quantity
        position["market_value"] += value
        position["snapshot_day"] = max(position["snapshot_day"], snapshot_day)

    fee_factor = Decimal("1") - Decimal(str(CM_FEE_PCT))
    net_value = gross_value * fee_factor
    positions = list(positions_by_key.values())
    for position in positions:
        position["share_pct"] = float(position["market_value"] / gross_value * 100) if gross_value else 0
    positions.sort(key=lambda position: position["market_value"], reverse=True)

    set_exposure = {}
    for position in positions:
        set_exposure[position["set_code"] or "Sans édition"] = (
            set_exposure.get(position["set_code"] or "Sans édition", 0)
            + position["market_value"]
        )

    set_exposure = sorted(set_exposure.items(), key=lambda item: item[1], reverse=True)
    for position in positions:
        position["market_value"] = float(position["market_value"])
    return {
        "cards": len(cards),
        "copies": copies,
        "asking_value": rounded_money(asking_value),
        "invested": rounded_money(invested),
        "priced_cost": rounded_money(priced_cost),
        "gross_value": rounded_money(gross_value),
        "net_value": rounded_money(net_value),
        "unrealized_net": rounded_money(known_cost_priced_value * fee_factor - priced_cost),
        "roi_pct": float(round((known_cost_priced_value * fee_factor - priced_cost) / priced_cost * 100, 1)) if priced_cost else None,
        "unknown_cost": unknown_cost,
        "missing_prices": missing_prices,
        "stale_prices": stale_prices,
        "coverage_pct": round(priced_cards / len(cards) * 100, 1) if cards else 0.0,
        "positions": positions,
        "set_exposure": [(name, float(value)) for name, value in set_exposure],
        "scenarios": [
            {
                "drop_pct": drop,
                "value_after_fees": rounded_money(gross_value * (1 - Decimal(drop) / 100) * fee_factor),
                "change_vs_now": rounded_money(-net_value * drop / 100),
                "profit_vs_cost": rounded_money(
                    known_cost_priced_value * (1 - Decimal(drop) / 100) * fee_factor - priced_cost
                ),
            }
            for drop in SCENARIO_DROPS
        ],
    }
