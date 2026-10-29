"""Rapport hebdomadaire Discord : valeur du stock, plus-value latente, mouvements et ventes."""
import argparse
import logging
from datetime import date, timedelta

import requests

from services import inventory, phase_one_monitor, sales_report
from services.inventory import calculate_net_margin

LOGGER = logging.getLogger("magic_nexus.weekly_report")
WINDOW_DAYS = 7
TOP_MOVERS = 3


def stock_totals(collection):
    value = cost = latent = 0.0
    references = 0
    for card in collection:
        if card.get("status", "FOR_SALE") != "FOR_SALE":
            continue
        quantity = max(1, int(card.get("quantity") or 1))
        selling = float(card.get("selling_price") or 0)
        purchase = float(card.get("purchase_price") or 0)
        value += selling * quantity
        cost += purchase * quantity
        latent += calculate_net_margin(selling, purchase)["net_profit"] * quantity
        references += 1
    return {
        "references": references, "value": round(value, 2),
        "cost": round(cost, 2), "latent_profit": round(latent, 2),
    }


def _point_day(point):
    try:
        return date.fromisoformat(str(point.get("snapshot_date") or point.get("observed_at"))[:10])
    except ValueError:
        return None


def price_movers(collection, today, history_for=inventory.get_variant_price_history):
    """Variation de prix sur 7 jours des cartes en vente, triée de la plus forte hausse à la plus forte baisse."""
    cutoff = today - timedelta(days=WINDOW_DAYS)
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


def build_report(collection, today=None, history_for=inventory.get_variant_price_history):
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
    fields = [
        {"name": "Stock en vente",
         "value": f"{totals['references']} référence(s) · valeur {totals['value']:.2f} €", "inline": False},
        {"name": "Plus-value latente (nette)", "value": f"{totals['latent_profit']:+.2f} €", "inline": True},
        {"name": "Capital investi", "value": f"{totals['cost']:.2f} €", "inline": True},
        {"name": "Ventes de la semaine",
         "value": (f"{sales['cards_sold']} carte(s) · CA {sales['revenue']:.2f} € · "
                   f"bénéfice net {sales['net_profit']:+.2f} €"), "inline": False},
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
