"""Classement explicable des alertes liées au stock."""
from datetime import date

PRICE_EVENT = "PRICE"
URGENT_IMPACT_EUR = 20.0
SIGNIFICANT_SHARE_PCT = 10.0


def _created_day(alert):
    try:
        return date.fromisoformat(str(alert.get("created_at") or "")[:10])
    except ValueError:
        return None


def _event_reliability(kind, source_url):
    kind = str(kind or "").upper()
    if "RUMOR" in kind or "HYPE" in kind:
        return "Faible · rumeur communautaire"
    if "SPOILER" in kind or "REPRINT" in kind or "BAN" in kind:
        return "À confirmer · veille événementielle"
    if kind == PRICE_EVENT:
        return "Indicatif · prix Scryfall"
    if source_url:
        return "Source disponible · à vérifier"
    return "Indéterminée"


def prioritize(alerts, collection, price_history, today=None):
    """Classe les alertes sur impact en euros, part de portefeuille et fraîcheur."""
    today = today or date.today()
    positions = {}
    card_quantities = {}
    card_values = {}
    for card in collection:
        if card.get("status", "FOR_SALE") != "FOR_SALE":
            continue
        key = (str(card.get("scryfall_id", "")), bool(card.get("foil")))
        quantity = max(1, int(card.get("quantity") or 1))
        positions[key] = positions.get(key, 0) + quantity
        card_quantities[key[0]] = card_quantities.get(key[0], 0) + quantity
        price_key = "eur_foil" if key[1] else "eur"
        card_history = price_history.get(key[0], {}) if key[0] else {}
        market = next(
            (card_history[day].get(price_key) for day in sorted(card_history, reverse=True)
             if card_history[day].get(price_key)),
            None,
        )
        if market is not None:
            card_values[key[0]] = card_values.get(key[0], 0.0) + float(market) * quantity

    gross = sum(card_values.values())

    ranked = []
    for alert in alerts:
        kind = alert.get("kind", PRICE_EVENT)
        key = (str(alert.get("scryfall_id", "")), bool(alert.get("foil")))
        variant_alert = kind in {PRICE_EVENT, "TARGET_REACHED"}
        in_stock = key in positions if variant_alert else key[0] in card_quantities
        quantity = (
            positions.get(key, 0) if variant_alert
            else card_quantities.get(key[0], 0)
        )
        previous = alert.get("previous_price")
        current = alert.get("current_price")
        if in_stock and kind == PRICE_EVENT and previous is not None and current is not None:
            impact = abs(float(current) - float(previous)) * quantity
        else:
            impact = None

        position_value = None
        if in_stock and current is not None and kind in {PRICE_EVENT, "TARGET_REACHED"}:
            position_value = float(current) * quantity
        elif in_stock:
            position_value = card_values.get(key[0])
        share = position_value / gross * 100 if position_value is not None and gross else 0.0
        created = _created_day(alert)
        age_days = (today - created).days if created else None
        fresh = age_days is not None and 0 <= age_days <= 7
        if not in_stock:
            priority = "Historique · hors stock"
        elif impact is not None and impact >= URGENT_IMPACT_EUR and fresh:
            priority = "À traiter en priorité"
        elif share >= SIGNIFICANT_SHARE_PCT and fresh:
            priority = "À surveiller de près"
        else:
            priority = "À suivre"

        ranked.append({
            **alert,
            "impact_eur": impact,
            "portfolio_share_pct": share,
            "age_days": age_days,
            "freshness": "Récente (≤ 7 j)" if fresh else "Ancienne ou date inconnue",
            "reliability": _event_reliability(kind, alert.get("source_url")),
            "priority": priority,
            "_rank": (impact or 0.0, share, int(fresh)),
        })
    ranked.sort(key=lambda item: item["_rank"], reverse=True)
    for item in ranked:
        item.pop("_rank")
    return ranked
