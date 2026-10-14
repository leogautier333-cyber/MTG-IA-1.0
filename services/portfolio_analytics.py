from datetime import date, datetime

from database import storage


CONDITION_DISCOUNTS = {"NM": 0.0, "EX": 0.03, "LP": 0.05, "GD": 0.08, "PL": 0.15, "PO": 0.25}
LIQUIDITY_SCORES = {"FAST": 1.0, "MEDIUM": 0.6, "SLOW": 0.2}


def _quantity(card):
    return max(1, int(card.get("quantity") or 1))


def _current_price(card):
    history = storage.get_variant_price_history(
        card.get("scryfall_id", ""), card.get("foil", False),
        card.get("language", "FR"), card.get("condition", "NM"),
    )
    if history:
        return float(history[-1]["price_eur"])
    return float(card.get("selling_price") or 0)


def _parse_day(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None


def simulate_sale(card, sale_price, shipping=0.0, cm_fee_pct=0.05,
                  condition_discount_pct=None):
    gross_price = max(0.0, float(sale_price or 0))
    quantity = _quantity(card)
    discount_pct = condition_discount_pct
    if discount_pct is None:
        discount_pct = CONDITION_DISCOUNTS.get(str(card.get("condition", "NM")).upper(), 0.0)
    discount_pct = min(1.0, max(0.0, float(discount_pct)))
    fee_pct = min(1.0, max(0.0, float(cm_fee_pct)))
    adjusted_price = gross_price * (1 - discount_pct)
    fee_per_card = adjusted_price * fee_pct
    net_per_card = adjusted_price - fee_per_card - max(0.0, float(shipping or 0))
    purchase_price = float(card.get("purchase_price") or 0)
    profit_per_card = net_per_card - purchase_price
    return {
        "gross_per_card": round(gross_price, 2),
        "condition_discount_pct": discount_pct * 100,
        "adjusted_per_card": round(adjusted_price, 2),
        "fee_per_card": round(fee_per_card, 2),
        "net_per_card": round(net_per_card, 2),
        "net_total": round(net_per_card * quantity, 2),
        "profit_per_card": round(profit_per_card, 2),
        "profit_total": round(profit_per_card * quantity, 2),
        "roi_pct": round(profit_per_card / purchase_price * 100, 1) if purchase_price > 0 else 0.0,
        "quantity": quantity,
    }


def analyze_portfolio(collection, today=None):
    today = today or date.today()
    held_cards = [card for card in collection if card.get("status") == "FOR_SALE"]
    sold_cards = [card for card in collection if card.get("status") == "SOLD"]
    invested = 0.0
    market_value = 0.0
    unrealized_profit = 0.0
    realized_profit = 0.0
    turnover = 0.0
    holding_days = []
    set_totals = {}
    format_totals = {}
    positions = []
    card_metrics = []
    liquidity_numerator = 0.0

    for card in collection:
        quantity = _quantity(card)
        purchase_price = float(card.get("purchase_price") or 0)
        cost_basis = purchase_price * quantity
        added = _parse_day(card.get("date_added"))
        sold = _parse_day(card.get("date_sold"))
        if added:
            holding_days.append(max(0, ((sold or today) - added).days))

        if card.get("status") == "FOR_SALE":
            current_price = _current_price(card)
            position_value = current_price * quantity
            position_profit = (current_price - purchase_price) * quantity
            market_value += position_value
            invested += cost_basis
            unrealized_profit += position_profit
            liquidity_numerator += position_value * LIQUIDITY_SCORES.get(
                str(card.get("liquidity_rating", "MEDIUM")).upper(), 0.5
            )
            positions.append({
                "name": card.get("name", "Carte"),
                "set_code": card.get("set_code", ""),
                "market_value": position_value,
                "unrealized_profit": position_profit,
                "roi_pct": (current_price - purchase_price) / purchase_price * 100 if purchase_price else 0.0,
                "weight_pct": 0.0,
            })
            card_metrics.append({
                "scryfall_id": card.get("scryfall_id"),
                "name": card.get("name", "Carte"),
                "set_code": card.get("set_code", ""),
                "quantity": quantity,
                "current_price": current_price,
                "unrealized_profit": position_profit,
                "roi_pct": (current_price - purchase_price) / purchase_price * 100 if purchase_price else 0.0,
            })
            sale = None
        elif card.get("status") == "SOLD":
            sale = simulate_sale(
                card, card.get("actual_sale_price") or 0,
                shipping=card.get("sale_shipping") or 0,
                condition_discount_pct=0.0,
            )
            realized_profit += sale["profit_total"]
            turnover += float(card.get("actual_sale_price") or 0) * quantity
            card_metrics.append({
                "scryfall_id": card.get("scryfall_id"),
                "name": card.get("name", "Carte"),
                "set_code": card.get("set_code", ""),
                "quantity": quantity,
                "realized_profit": sale["profit_total"],
                "roi_pct": sale["roi_pct"],
            })
        else:
            continue

        unit_profit = sale["profit_per_card"] if sale else current_price - purchase_price
        group = set_totals.setdefault(card.get("set_code", "unknown"), {"invested": 0.0, "profit": 0.0})
        group["invested"] += cost_basis
        group["profit"] += unit_profit * quantity
        for format_name, legality in (card.get("legalities") or {}).items():
            if legality != "legal":
                continue
            format_group = format_totals.setdefault(format_name, {"invested": 0.0, "profit": 0.0})
            format_group["invested"] += cost_basis
            format_group["profit"] += unit_profit * quantity

    positions.sort(key=lambda item: item["market_value"], reverse=True)
    for position in positions:
        position["weight_pct"] = position["market_value"] / market_value * 100 if market_value else 0.0
    top_ten_value = sum(position["market_value"] for position in positions[:10])
    concentration = top_ten_value / market_value * 100 if market_value else 0.0
    for grouped in (set_totals, format_totals):
        for values in grouped.values():
            values["roi_pct"] = values["profit"] / values["invested"] * 100 if values["invested"] else 0.0

    return {
        "stock_value": market_value,
        "invested_capital": invested,
        "turnover": turnover,
        "realized_profit": realized_profit,
        "realized_roi_pct": realized_profit / sum(
            float(card.get("purchase_price") or 0) * _quantity(card) for card in sold_cards
        ) * 100 if sold_cards and sum(float(card.get("purchase_price") or 0) * _quantity(card) for card in sold_cards) else 0.0,
        "unrealized_profit": unrealized_profit,
        "unrealized_roi_pct": unrealized_profit / invested * 100 if invested else 0.0,
        "for_sale_count": len(held_cards),
        "sold_count": len(sold_cards),
        "average_holding_days": sum(holding_days) / len(holding_days) if holding_days else 0.0,
        "top_ten_concentration_pct": concentration,
        "liquidity_index": liquidity_numerator / market_value * 100 if market_value else 0.0,
        "by_set": set_totals,
        "by_format": format_totals,
        "by_card": card_metrics,
        "top_positions": positions[:10],
    }


def suggest_arbitrage(collection, max_suggestions=5):
    suggestions = []
    for card in collection:
        if card.get("status") != "FOR_SALE" or not card.get("scryfall_id"):
            continue
        current_price = _current_price(card)
        if current_price <= 0:
            continue
        sale = simulate_sale(card, current_price)
        quantity = sale["quantity"]
        for alternative in storage.get_alternative_printings(card["scryfall_id"]):
            target_price = alternative.get("price_eur_foil" if card.get("foil") else "price_eur")
            if target_price is None or target_price <= 0 or target_price >= current_price:
                continue
            spread = sale["net_total"] - float(target_price) * quantity
            if spread <= 0:
                continue
            suggestions.append({
                "card_name": card.get("name", alternative["name"]),
                "sell_set": card.get("set_code", ""),
                "buy_set": alternative.get("set_code", ""),
                "quantity": quantity,
                "sale_net_eur": sale["net_total"],
                "replacement_cost_eur": round(float(target_price) * quantity, 2),
                "estimated_spread_eur": round(spread, 2),
                "source": "Prix Scryfall indicatifs; disponibilité et frais de marché non vérifiés.",
            })
    suggestions.sort(key=lambda item: item["estimated_spread_eur"], reverse=True)
    return suggestions[:max_suggestions]


def portfolio_summary(collection):
    return analyze_portfolio(collection)