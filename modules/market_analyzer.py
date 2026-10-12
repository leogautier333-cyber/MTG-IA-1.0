from itertools import combinations


def portfolio_summary(collection):
    for_sale = [card for card in collection if card.get("status") == "FOR_SALE"]
    sold = [card for card in collection if card.get("status") == "SOLD"]
    invested = sum(
        float(card.get("purchase_price") or 0) * int(card.get("quantity") or 1)
        for card in sold
    )
    realized = sum(
        (
            float(card.get("actual_sale_price") or 0) * 0.95
            - float(card.get("purchase_price") or 0)
        ) * int(card.get("quantity") or 1)
        for card in sold
    )
    return {
        "stock_value": sum(
            float(card.get("selling_price") or 0) * int(card.get("quantity") or 1)
            for card in for_sale
        ),
        "invested_capital": sum(
            float(card.get("purchase_price") or 0) * int(card.get("quantity") or 1)
            for card in for_sale
        ),
        "turnover": sum(
            float(card.get("actual_sale_price") or 0) * int(card.get("quantity") or 1)
            for card in sold
        ),
        "realized_profit": realized,
        "realized_roi_pct": realized / invested * 100 if invested else 0.0,
        "for_sale_count": len(for_sale),
        "sold_count": len(sold),
    }


def detect_watchlist_drops(watchlist, collection, observations, threshold_pct=15.0):
    owned_ids = {card.get("scryfall_id") for card in collection}
    alerts = []
    for card in watchlist:
        card_id = card.get("scryfall_id")
        if not card_id or card_id in owned_ids:
            continue
        history = observations.get(card_id, [])
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
                "previous_price": previous,
                "current_price": current,
                "change_pct": change_pct,
                "currency_source": "Scryfall EUR",
            })
    return alerts


def analyze_buyout_signal(price_points, volume_points=None):
    """Avoid claiming a buyout without transaction-volume evidence."""
    if len(price_points) < 2:
        return {"status": "INSUFFICIENT_DATA", "reason": "Deux observations de prix minimum sont nécessaires."}

    previous = float(price_points[-2])
    current = float(price_points[-1])
    if previous <= 0:
        return {"status": "INSUFFICIENT_DATA", "reason": "Prix de référence invalide."}

    change_pct = (current - previous) / previous * 100
    if change_pct < 30:
        return {"status": "NO_SPIKE", "change_pct": change_pct, "reason": "Aucune hausse de 30 % entre les deux observations."}
    if not volume_points or len(volume_points) < 2:
        return {
            "status": "INCONCLUSIVE",
            "change_pct": change_pct,
            "reason": "Hausse rapide observée, mais le volume réel de ventes Cardmarket n'est pas disponible.",
        }

    previous_volume = float(volume_points[-2])
    current_volume = float(volume_points[-1])
    if current_volume <= previous_volume:
        return {
            "status": "SUSPICIOUS",
            "change_pct": change_pct,
            "reason": "Le prix augmente fortement sans hausse du volume de ventes fourni.",
        }
    return {
        "status": "DEMAND_SUPPORTED",
        "change_pct": change_pct,
        "reason": "Le prix et le volume de ventes fourni augmentent ensemble.",
    }


def suggest_exchange_lot(collection, target_value, max_cards=3, max_candidates=24):
    candidates = [
        card for card in collection
        if card.get("status") == "FOR_SALE" and float(card.get("selling_price") or 0) > 0
    ][:max_candidates]
    options = []
    for size in range(1, min(max_cards, len(candidates)) + 1):
        for lot in combinations(candidates, size):
            value = sum(float(card.get("selling_price") or 0) for card in lot)
            options.append((abs(value - target_value), value, lot))
    options.sort(key=lambda option: (option[0], len(option[2])))
    return [
        {"difference": difference, "value": value, "cards": [card.get("name", "Carte") for card in lot]}
        for difference, value, lot in options[:3]
    ]