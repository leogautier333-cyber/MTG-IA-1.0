"""Valeur du stock dans le temps, reconstruite à partir des relevés de prix enregistrés."""
from bisect import bisect_right


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


def value_series(collection, price_history):
    """Une ligne par jour de relevé : valeur de marché et capital investi des cartes détenues ce jour-là.

    Les cartes sans relevé de prix à une date donnée ne sont pas comptées dans la valeur."""
    cards = [c for c in collection if c.get("scryfall_id")]
    days = sorted({
        day for card in cards for day in (price_history.get(str(card["scryfall_id"])) or {})
    })
    price_dates = {}
    for card in cards:
        card_id = str(card["scryfall_id"])
        key = "eur_foil" if card.get("foil") else "eur"
        variant = (card_id, key)
        if variant not in price_dates:
            history = price_history.get(card_id) or {}
            price_dates[variant] = sorted(day for day, point in history.items() if point.get(key))
    series = []
    for day in days:
        value = cost = 0.0
        for card in cards:
            if not _was_held(card, day):
                continue
            quantity = max(1, int(card.get("quantity") or 1))
            cost += float(card.get("purchase_price") or 0) * quantity
            key = "eur_foil" if card.get("foil") else "eur"
            card_id = str(card["scryfall_id"])
            history = price_history.get(card_id) or {}
            known = price_dates[(card_id, key)]
            index = bisect_right(known, day) - 1
            if index >= 0:
                value += float(history[known[index]][key]) * quantity
        series.append({"date": day, "value": round(value, 2), "cost": round(cost, 2)})
    return series
