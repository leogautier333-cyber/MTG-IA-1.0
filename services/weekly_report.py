"""Rapport hebdomadaire Discord : valeur du stock, plus-value latente, mouvements et ventes."""
import argparse
import logging
from datetime import date, timedelta
from decimal import Decimal

import requests

from database import storage
from services import inventory, phase_one_monitor, sales_report
from services.inventory import calculate_net_margin
from services.money import decimal_amount, rounded_money

LOGGER = logging.getLogger("magic_nexus.weekly_report")
WINDOW_DAYS = 7
TOP_MOVERS = 3


def stock_totals(collection):
    value = cost = latent = Decimal("0")
    references = unknown_cost = 0
    for card in collection:
        if card.get("status", "FOR_SALE") != "FOR_SALE":
            continue
        quantity = max(1, int(card.get("quantity") or 1))
        selling = float(card.get("selling_price") or 0)
        value += decimal_amount(selling) * quantity
        if card.get("purchase_price_unknown"):
            unknown_cost += 1
        else:
            purchase = float(card.get("purchase_price") or 0)
            cost += decimal_amount(purchase) * quantity
            latent += decimal_amount(calculate_net_margin(selling, purchase)["net_profit"]) * quantity
        references += 1
    return {
        "references": references, "value": rounded_money(value),
        "cost": rounded_money(cost), "latent_profit": rounded_money(latent),
        "unknown_cost": unknown_cost,
    }


def _point_day(point):
    try:
        return date.fromisoformat(str(point.get("snapshot_date") or point.get("observed_at"))[:10])
    except ValueError:
        return None


def price_movers(collection, today, history_for=None):
    """Variation de prix sur 7 jours des cartes en vente, triée de la plus forte hausse à la plus forte baisse."""
    cutoff = today - timedelta(days=WINDOW_DAYS)
    if history_for is None:
        variants = {
            (str(card["scryfall_id"]), bool(card.get("foil")), "FR", "NM")
            for card in collection
            if card.get("status", "FOR_SALE") == "FOR_SALE" and card.get("scryfall_id")
        }
        points_by_variant = storage.get_price_points_batch(variants)

        def history_for(card_id, foil=False):
            return [
                {"snapshot_date": day, "price_eur": price}
                for day, price in points_by_variant[(card_id, foil, "FR", "NM")]
            ]

    seen, movers = set(), []
    for card in collection:
        if card.get("status", "FOR_SALE") != "FOR_SALE" or not card.get("scryfall_id"):
            continue
        key = (str(card["scryfall_id"]), bool(card.get("foil")))
        if key in seen:
            continue
        seen.add(key)
        points = [
            (day, float(point["price_eur"])) for point in history_for(key[0], foil=key[1])
            if (day := _point_day(point)) and point.get("price_eur")
        ]
        points.sort()
        baseline = next(((d, p) for d, p in reversed(points) if d <= cutoff), None)
        if not baseline or baseline[1] <= 0 or not points:
            continue
        latest = points[-1][1]
        movers.append({
            "name": card.get("name", "Carte inconnue"), "price": latest,
            "change_pct": (latest - baseline[1]) / baseline[1] * 100,
        })
    return sorted(movers, key=lambda mover: mover["change_pct"], reverse=True)


def build_report(collection, today=None, history_for=None):
    today = today or date.today()
    movers = price_movers(collection, today, history_for)
    return {
        "totals": stock_totals(collection),
        "sales": sales_report.sales_since(collection, today - timedelta(days=WINDOW_DAYS)),
        "risers": [m for m in movers[:TOP_MOVERS] if m["change_pct"] > 0],
        "fallers": [m for m in reversed(movers[-TOP_MOVERS:]) if m["change_pct"] < 0],
    }


def _mover_lines(movers):
    return "\n".join(
        f"{m['name']} : {m['change_pct']:+.1f} % ({m['price']:.2f} €)" for m in movers
    ) or "Aucun mouvement notable."


def report_embed(report):
    totals, sales = report["totals"], report["sales"]
    stock_cost_label = (f" · {totals['unknown_cost']} coût(s) inconnu(s)"
                        if totals["unknown_cost"] else "")
    sales_profit = (f"{sales['net_profit']:+.2f} €" if sales["net_profit"] is not None
                    else "indisponible")
    if sales["unknown_cost_sales"]:
        sales_profit += f" (partiel : {sales['unknown_cost_sales']} coût(s) inconnu(s))"
    stock_cost_available = totals["references"] == 0 or totals["unknown_cost"] < totals["references"]
    latent = f"{totals['latent_profit']:+.2f} €" if stock_cost_available else "indisponible"
    cost = f"{totals['cost']:.2f} €" if stock_cost_available else "indisponible"
    fields = [
        {"name": "Stock en vente",
         "value": f"{totals['references']} référence(s) · valeur {totals['value']:.2f} €", "inline": False},
        {"name": "Plus-value latente (nette)",
         "value": f"{latent}{stock_cost_label}", "inline": True},
        {"name": "Capital investi", "value": f"{cost}{stock_cost_label}", "inline": True},
        {"name": "Ventes de la semaine",
         "value": (f"{sales['cards_sold']} carte(s) · CA {sales['revenue']:.2f} € · "
                   f"bénéfice net {sales_profit}"), "inline": False},
        {"name": "Plus fortes hausses (7 j)", "value": _mover_lines(report["risers"]), "inline": False},
        {"name": "Plus fortes baisses (7 j)", "value": _mover_lines(report["fallers"]), "inline": False},
    ]
    return "[RAPPORT HEBDOMADAIRE] Magic Trading & Profit Manager", "Résumé de ta semaine.", fields


def send_weekly_report(session=requests, today=None):
    report = build_report(inventory.get_collection(), today=today)
    title, description, fields = report_embed(report)
    phase_one_monitor._send_webhook(title, description, session=session, fields=fields)
    return report


def main():
    argparse.ArgumentParser(description="Envoie le rapport hebdomadaire du stock sur Discord.").parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    report = send_weekly_report()
    print(f"Rapport envoyé : {report['totals']['references']} référence(s), "
          f"{report['sales']['cards_sold']} vente(s) cette semaine.")


if __name__ == "__main__":
    main()
