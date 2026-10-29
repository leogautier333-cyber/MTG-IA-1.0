"""Bilan réel des ventes : chiffre d'affaires, frais Cardmarket, bénéfice net et rentabilité."""
from datetime import date

from services.inventory import calculate_net_margin


def _sold_day(card):
    try:
        return date.fromisoformat(str(card.get("date_sold") or "")[:10])
    except ValueError:
        return None


def sale_lines(collection):
    """Une ligne par carte vendue, avec les montants totaux (quantité incluse)."""
    lines = []
    for card in collection:
        if card.get("status") != "SOLD":
            continue
        quantity = max(1, int(card.get("quantity") or 1))
        sale_price = float(card.get("actual_sale_price") or 0)
        purchase_price = float(card.get("purchase_price") or 0)
        margin = calculate_net_margin(sale_price, purchase_price)
        lines.append({
            "name": card.get("name", "Carte inconnue"),
            "set_code": str(card.get("set_code", "")).upper(),
            "sold_on": _sold_day(card),
            "quantity": quantity,
            "revenue": sale_price * quantity,
            "fees": margin["cm_fee"] * quantity,
            "cost": purchase_price * quantity,
            "net_profit": margin["net_profit"] * quantity,
        })
    return lines


def summarize(lines):
    revenue = sum(line["revenue"] for line in lines)
    cost = sum(line["cost"] for line in lines)
    net_profit = sum(line["net_profit"] for line in lines)
    return {
        "cards_sold": sum(line["quantity"] for line in lines),
        "revenue": round(revenue, 2),
        "fees": round(sum(line["fees"] for line in lines), 2),
        "cost": round(cost, 2),
        "net_profit": round(net_profit, 2),
        "roi_percent": round(net_profit / cost * 100, 1) if cost > 0 else 0.0,
    }


def monthly_summary(collection):
    """Bilan par mois (du plus récent au plus ancien) ; les ventes sans date sont ignorées."""
    by_month = {}
    for line in sale_lines(collection):
        if line["sold_on"]:
            by_month.setdefault(line["sold_on"].strftime("%Y-%m"), []).append(line)
    return [
        {"month": month, **summarize(lines)}
        for month, lines in sorted(by_month.items(), reverse=True)
    ]


def sales_since(collection, start_day):
    return summarize([
        line for line in sale_lines(collection)
        if line["sold_on"] and line["sold_on"] >= start_day
    ])
