def detect_watchlist_drops(watchlist, collection, observations, threshold_pct=15.0):
    owned_ids = {card.get("scryfall_id") for card in collection}
    alerts = []
    for card in watchlist:
        card_id = card.get("scryfall_id")
        if not card_id or card_id in owned_ids:
            continue
        variant_key = (
            card_id, bool(card.get("foil")), str(card.get("language", "FR")),
            str(card.get("condition", "NM")),
        )
        history = observations.get(variant_key, observations.get(card_id, []))
        if len(history) < 2:
            continue
        previous = history[-2].get("price_eur")
        current = history[-1].get("price_eur")
        if previous is None or current is None or previous <= 0:
            continue
        change_pct = (current - previous) / previous * 100
        if change_pct <= -abs(threshold_pct):
            alerts.append({
                "name": card.get("name", "Carte inconnue"),
                "scryfall_id": card_id,
                "foil": bool(card.get("foil")),
                "language": card.get("language", "FR"),
                "condition": card.get("condition", "NM"),
                "previous_price": previous,
                "current_price": current,
                "change_pct": change_pct,
                "currency_source": "Scryfall EUR",
            })
    return alerts
