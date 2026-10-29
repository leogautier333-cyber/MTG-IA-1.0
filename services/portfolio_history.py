"""Valeur du stock dans le temps, reconstruite à partir des relevés de prix enregistrés."""


def _day(value):
    return str(value or "")[:10]


def _was_held(card, day):
    added = _day(card.get("date_added"))
    if added and added > day:
        return False
    return not (card.get("status") == "SOLD" and _day(card.get("date_sold")) and _day(card.get("date_sold")) <= day)


def value_series(collection, price_history):
    """Une ligne par jour de relevé : valeur de marché et capital investi des cartes détenues ce jour-là.

    Les cartes sans relevé de prix à une date donnée ne sont pas comptées dans la valeur."""
    cards = [c for c in collection if c.get("scryfall_id")]
    days = sorted({
        day for card in cards for day in (price_history.get(str(card["scryfall_id"])) or {})
    })
    series = []
    for day in days:
        value = cost = 0.0
        for card in cards:
            if not _was_held(card, day):
                continue
            quantity = max(1, int(card.get("quantity") or 1))
            cost += float(card.get("purchase_price") or 0) * quantity
            key = "eur_foil" if card.get("foil") else "eur"
            history = price_history.get(str(card["scryfall_id"])) or {}
            known = [d for d in history if d <= day and history[d].get(key)]
            if known:
                value += float(history[max(known)][key]) * quantity
        series.append({"date": day, "value": round(value, 2), "cost": round(cost, 2)})
    return series
