from datetime import date, datetime
from decimal import Decimal

from database import market_data, storage
from services.inventory import calculate_net_margin
from services.money import decimal_amount, rounded_money


CONDITION_DISCOUNTS = {"NM": 0.0, "EX": 0.03, "LP": 0.05, "GD": 0.08, "PL": 0.15, "PO": 0.25}
LIQUIDITY_SCORES = {"FAST": 1.0, "MEDIUM": 0.6, "SLOW": 0.2}


def _quantity(card):
    return max(1, int(card.get("quantity") or 1))


def _variant_key(card):
    return (
        str(card.get("scryfall_id") or ""), bool(card.get("foil")),
        str(card.get("language", "FR")), str(card.get("condition", "NM")),
    )


def _current_price(card, points_by_variant=None):
    key = _variant_key(card)
    if points_by_variant is not None:
        points = points_by_variant.get(key, [])
        if points:
            return float(points[-1][1])
        return float(card.get("selling_price") or 0)
    history = storage.get_variant_price_history(*key)
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
    adjusted_price = Decimal(str(gross_price)) * (1 - Decimal(str(discount_pct)))
    shipping = max(0.0, float(shipping or 0))
    purchase_price = float(card.get("purchase_price") or 0)
    proceeds = calculate_net_margin(adjusted_price, 0, shipping, fee_pct)
    margin = calculate_net_margin(adjusted_price, purchase_price, shipping, fee_pct)
    net_per_card = proceeds["net_profit"]
    profit_per_card = None if card.get("purchase_price_unknown") else margin["net_profit"]
    return {
        "gross_per_card": round(gross_price, 2),
        "condition_discount_pct": discount_pct * 100,
        "adjusted_per_card": float(round(adjusted_price, 2)),
        "fee_per_card": proceeds["cm_fee"],
        "net_per_card": net_per_card,
        "net_total": float(Decimal(str(net_per_card)) * quantity),
        "profit_per_card": profit_per_card,
        "profit_total": float(Decimal(str(profit_per_card)) * quantity) if profit_per_card is not None else None,
        "roi_pct": margin["roi_percent"]
        if profit_per_card is not None and purchase_price > 0 else None,
        "quantity": quantity,
    }


def analyze_portfolio(collection, today=None):
    today = today or date.today()
    held_cards = [card for card in collection if card.get("status") == "FOR_SALE"]
    sold_cards = [card for card in collection if card.get("status") == "SOLD"]
    points_by_variant = storage.get_price_points_batch(
        _variant_key(card) for card in held_cards if card.get("scryfall_id")
    )
    held_unknown_cost = sum(bool(card.get("purchase_price_unknown")) for card in held_cards)
    sold_unknown_cost = sum(bool(card.get("purchase_price_unknown")) for card in sold_cards)
    sold_known_cost = sum((
        decimal_amount(card.get("purchase_price") or 0) * _quantity(card)
        for card in sold_cards if not card.get("purchase_price_unknown")
    ), Decimal("0"))
    invested = market_value = unrealized_profit = realized_profit = turnover = Decimal("0")
    holding_days = []
    set_totals = {}
    format_totals = {}
    positions = []
    card_metrics = []
    liquidity_numerator = Decimal("0")

    for card in collection:
        quantity = _quantity(card)
        purchase_price = decimal_amount(card.get("purchase_price") or 0)
        cost_known = not card.get("purchase_price_unknown")
        cost_basis = purchase_price * quantity
        added = _parse_day(card.get("date_added"))
        sold = _parse_day(card.get("date_sold"))
        if added:
            holding_days.append(max(0, ((sold or today) - added).days))

        if card.get("status") == "FOR_SALE":
            current_price = decimal_amount(_current_price(card, points_by_variant))
            position_value = current_price * quantity
            position_profit = (current_price - purchase_price) * quantity if cost_known else None
            market_value += position_value
            if cost_known:
                invested += cost_basis
                unrealized_profit += position_profit
            liquidity_numerator += position_value * decimal_amount(
                LIQUIDITY_SCORES.get(str(card.get("liquidity_rating", "MEDIUM")).upper(), 0.5)
            )
            positions.append({
                "name": card.get("name", "Carte"),
                "set_code": card.get("set_code", ""),
                "market_value": float(position_value),
                "unrealized_profit": float(position_profit) if position_profit is not None else None,
                "roi_pct": float((current_price - purchase_price) / purchase_price * 100)
                if cost_known and purchase_price else None,
                "weight_pct": 0.0,
            })
            card_metrics.append({
                "scryfall_id": card.get("scryfall_id"),
                "name": card.get("name", "Carte"),
                "set_code": card.get("set_code", ""),
                "quantity": quantity,
                "current_price": float(current_price),
                "unrealized_profit": float(position_profit) if position_profit is not None else None,
                "roi_pct": float((current_price - purchase_price) / purchase_price * 100)
                if cost_known and purchase_price else None,
            })
            sale = None
        elif card.get("status") == "SOLD":
            sale = simulate_sale(
                card, card.get("actual_sale_price") or 0,
                shipping=card.get("sale_shipping") or 0,
                condition_discount_pct=0.0,
            )
            if sale["profit_total"] is not None:
                realized_profit += decimal_amount(sale["profit_total"])
            turnover += decimal_amount(card.get("actual_sale_price") or 0) * quantity
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

        if not cost_known:
            continue
        unit_profit = decimal_amount(sale["profit_per_card"]) if sale else current_price - purchase_price
        group = set_totals.setdefault(card.get("set_code", "unknown"), {"invested": Decimal("0"), "profit": Decimal("0")})
        group["invested"] += cost_basis
        group["profit"] += unit_profit * quantity
        for format_name, legality in (card.get("legalities") or {}).items():
            if legality != "legal":
                continue
            format_group = format_totals.setdefault(format_name, {"invested": Decimal("0"), "profit": Decimal("0")})
            format_group["invested"] += cost_basis
            format_group["profit"] += unit_profit * quantity

    positions.sort(key=lambda item: item["market_value"], reverse=True)
    for position in positions:
        position["weight_pct"] = float(decimal_amount(position["market_value"]) / market_value * 100) if market_value else 0.0
    top_ten_value = sum((decimal_amount(position["market_value"]) for position in positions[:10]), Decimal("0"))
    concentration = float(top_ten_value / market_value * 100) if market_value else 0.0
    for grouped in (set_totals, format_totals):
        for values in grouped.values():
            values["roi_pct"] = float(values["profit"] / values["invested"] * 100) if values["invested"] else 0.0
            values["invested"] = float(values["invested"])
            values["profit"] = float(values["profit"])

    return {
        "stock_value": float(market_value),
        "invested_capital": float(invested),
        "turnover": float(turnover),
        "realized_profit": float(realized_profit),
        "realized_roi_pct": float(realized_profit / sold_known_cost * 100) if sold_known_cost else 0.0,
        "unrealized_profit": float(unrealized_profit),
        "unrealized_roi_pct": float(unrealized_profit / invested * 100) if invested else 0.0,
        "for_sale_count": len(held_cards),
        "sold_count": len(sold_cards),
        "held_unknown_cost": held_unknown_cost,
        "sold_unknown_cost": sold_unknown_cost,
        "average_holding_days": sum(holding_days) / len(holding_days) if holding_days else 0.0,
        "top_ten_concentration_pct": concentration,
        "liquidity_index": float(liquidity_numerator / market_value * 100) if market_value else 0.0,
        "by_set": set_totals,
        "by_format": format_totals,
        "by_card": card_metrics,
        "top_positions": positions[:10],
    }


def suggest_arbitrage(collection, max_suggestions=5):
    suggestions = []
    points_by_variant = storage.get_price_points_batch(
        _variant_key(card) for card in collection
        if card.get("status") == "FOR_SALE" and card.get("scryfall_id")
    )
    for card in collection:
        if card.get("status") != "FOR_SALE" or not card.get("scryfall_id"):
            continue
        current_price = _current_price(card, points_by_variant)
        if current_price <= 0:
            continue
        sale = simulate_sale(card, current_price)
        quantity = sale["quantity"]
        for alternative in market_data.get_alternative_printings(card["scryfall_id"]):
            target_price = alternative.get("price_eur_foil" if card.get("foil") else "price_eur")
            if target_price is None or target_price <= 0 or target_price >= current_price:
                continue
            spread = decimal_amount(sale["net_total"]) - decimal_amount(target_price) * quantity
            if spread <= 0:
                continue
            suggestions.append({
                "card_name": card.get("name", alternative["name"]),
                "sell_set": card.get("set_code", ""),
                "buy_set": alternative.get("set_code", ""),
                "quantity": quantity,
                "sale_net_eur": sale["net_total"],
                "replacement_cost_eur": rounded_money(decimal_amount(target_price) * quantity),
                "estimated_spread_eur": rounded_money(spread),
                "source": "Prix Scryfall indicatifs; disponibilité et frais de marché non vérifiés.",
            })
    suggestions.sort(key=lambda item: item["estimated_spread_eur"], reverse=True)
    return suggestions[:max_suggestions]


def portfolio_summary(collection):
    return analyze_portfolio(collection)