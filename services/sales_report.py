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
            "scryfall_id": str(card.get("scryfall_id", "")),
            "foil": bool(card.get("foil")),
            "set_code": str(card.get("set_code", "")).upper(),
            "sold_on": _sold_day(card),
            "quantity": quantity,
            "revenue": sale_price * quantity,
            "fees": margin["cm_fee"] * quantity,
            "cost": purchase_price * quantity,
            "net_profit": margin["net_profit"] * quantity,
        })
    return lines


def compare_to_market(lines, price_history):
    """Compare le prix réellement obtenu au dernier snapshot connu au plus tard le jour de vente."""
    for line in lines:
        sold_day = line["sold_on"].isoformat() if line["sold_on"] else None
        days = price_history.get(line["scryfall_id"], {}) if sold_day else {}
        key = "eur_foil" if line["foil"] else "eur"
        known_days = [
            day for day, point in days.items()
            if day <= sold_day and point.get(key) is not None
        ]
        reference_day = max(known_days, default=None)
        reference = days[reference_day].get(key) if reference_day else None
        actual = line["revenue"] / line["quantity"]
        line["market_reference"] = float(reference) if reference is not None else None
        line["market_reference_day"] = reference_day
        line["sale_vs_market"] = (
            round((actual - float(reference)) * line["quantity"], 2)
            if reference is not None else None
        )
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
        "sale_vs_market": round(sum(
            line.get("sale_vs_market") or 0 for line in lines
        ), 2),
        "market_compared_sales": sum(
            1 for line in lines if line.get("market_reference") is not None
        ),
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
