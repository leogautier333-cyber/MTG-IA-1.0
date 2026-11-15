"""Valeur du stock dans le temps, reconstruite à partir des relevés de prix enregistrés."""
from bisect import bisect_right
from decimal import Decimal

from services.money import decimal_amount, rounded_money


def _day(value):
    return str(value or "")[:10]


def _was_held(card, day):
    added = _day(card.get("date_added"))
    if added and added > day:
        return False
    if card.get("status") == "SOLD":
        end = _day(card.get("date_sold"))
    elif card.get("status") == "WITHDRAWN":
        end = _day(card.get("date_withdrawn"))
    else:
        end = ""
    return not (end and end <= day)


def value_series(collection, price_history, inventory_events=None):
    """Une ligne par jour de relevé : valeur de marché et capital investi des cartes détenues ce jour-là.

    Les cartes sans relevé de prix à une date donnée ne sont pas comptées dans la valeur."""
    events = sorted(inventory_events or [], key=lambda event: _day(event["occurred_at"]))
    cards = [c for c in collection if c.get("scryfall_id")]
    history_cards = cards + [
        card for event in events for card in (event["old"], event["new"])
        if card and card.get("scryfall_id")
    ]
    days = sorted({
        day for card in history_cards for day in (price_history.get(str(card["scryfall_id"])) or {})
    })
    price_dates = {}
    for card in history_cards:
        card_id = str(card["scryfall_id"])
        key = "eur_foil" if card.get("foil") else "eur"
        variant = (card_id, key)
        if variant not in price_dates:
            history = price_history.get(card_id) or {}
            price_dates[variant] = sorted(day for day, point in history.items() if point.get(key))
    start_day = _day(events[0]["occurred_at"]) if events else None
    active = {}
    event_index = 0

    def apply_card(card, multiplier):
        if not card or card.get("status", "FOR_SALE") != "FOR_SALE" or not card.get("scryfall_id"):
            return
        key = (str(card["scryfall_id"]), "eur_foil" if card.get("foil") else "eur")
        quantity = max(1, int(card.get("quantity") or 1))
        position = active.setdefault(key, {"quantity": 0, "cost": Decimal("0")})
        position["quantity"] += multiplier * quantity
        if not card.get("purchase_price_unknown"):
            position["cost"] += multiplier * decimal_amount(card.get("purchase_price") or 0) * quantity

    series = []
    for day in days:
        while event_index < len(events) and _day(events[event_index]["occurred_at"]) <= day:
            event = events[event_index]
            apply_card(event["old"], -1)
            apply_card(event["new"], 1)
            event_index += 1
        if start_day and day >= start_day:
            value = cost = Decimal("0")
            for (card_id, key), position in active.items():
                if position["quantity"] <= 0:
                    continue
                cost += position["cost"]
                history = price_history.get(card_id) or {}
                known = price_dates.get((card_id, key))
                if known is None:
                    known = sorted(d for d, point in history.items() if point.get(key))
                    price_dates[(card_id, key)] = known
                index = bisect_right(known, day) - 1
                if index >= 0:
                    value += decimal_amount(history[known[index]][key]) * position["quantity"]
            series.append({"date": day, "value": rounded_money(value), "cost": rounded_money(cost)})
            continue
        value = cost = Decimal("0")
        for card in cards:
            if not _was_held(card, day):
                continue
            quantity = max(1, int(card.get("quantity") or 1))
            if not card.get("purchase_price_unknown"):
                cost += decimal_amount(card.get("purchase_price") or 0) * quantity
            key = "eur_foil" if card.get("foil") else "eur"
            card_id = str(card["scryfall_id"])
            history = price_history.get(card_id) or {}
            known = price_dates[(card_id, key)]
            index = bisect_right(known, day) - 1
            if index >= 0:
                value += decimal_amount(history[known[index]][key]) * quantity
        series.append({"date": day, "value": rounded_money(value), "cost": rounded_money(cost)})
    return series
