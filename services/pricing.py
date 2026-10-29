"""Suggestion de prix de vente : prix du marché, frais Cardmarket et marge minimale souhaitée."""
CM_FEE_PCT = 0.05
TOLERANCE = 0.10

LABELS = {
    "NO_DATA": "Pas de prix du marché",
    "BELOW_MIN": "Marché sous ton seuil de marge",
    "TOO_HIGH": "Trop cher vs marché",
    "TOO_LOW": "Trop bas vs marché",
    "ALIGNED": "Aligné",
}


def minimum_price(purchase_price, min_margin_pct, fee_pct=CM_FEE_PCT):
    """Prix de vente minimal pour garder `min_margin_pct` % de bénéfice net après frais."""
    return float(purchase_price or 0) * (1 + min_margin_pct / 100) / (1 - fee_pct)


def suggest(market_price, purchase_price, selling_price, min_margin_pct=15.0):
    if not market_price or market_price <= 0:
        return {"status": "NO_DATA", "suggested": None, "label": LABELS["NO_DATA"]}
    floor = minimum_price(purchase_price, min_margin_pct)
    suggested = round(max(float(market_price), floor), 2)
    selling_price = float(selling_price or 0)
    if market_price < floor:
        status = "BELOW_MIN"
    elif selling_price > market_price * (1 + TOLERANCE):
        status = "TOO_HIGH"
    elif selling_price < market_price * (1 - TOLERANCE):
        status = "TOO_LOW"
    else:
        status = "ALIGNED"
    return {"status": status, "suggested": suggested, "label": LABELS[status]}


def latest_market_price(price_history, scryfall_id, foil=False):
    """Dernier relevé connu (price_history : {id: {date: {"eur": x, "eur_foil": y}}})."""
    days = price_history.get(str(scryfall_id)) or {}
    key = "eur_foil" if foil else "eur"
    for day in sorted(days, reverse=True):
        price = days[day].get(key)
        if price:
            return float(price)
    return None


def suggestions(collection, price_history, min_margin_pct=15.0):
    """Une ligne par carte en vente, avec prix du marché, prix fixé et suggestion."""
    lines = []
    for card in collection:
        if card.get("status", "FOR_SALE") != "FOR_SALE" or not card.get("scryfall_id"):
            continue
        market = latest_market_price(price_history, card["scryfall_id"], bool(card.get("foil")))
        advice = suggest(market, card.get("purchase_price"), card.get("selling_price"), min_margin_pct)
        lines.append({
            "name": card.get("name", "Carte inconnue"), "foil": bool(card.get("foil")),
            "purchase_price": float(card.get("purchase_price") or 0),
            "selling_price": float(card.get("selling_price") or 0),
            "market_price": market, **advice,
        })
    return lines
