"""IA à règles : explique une variation de prix d'une carte du stock et donne un verdict d'action.

Aucun appel externe : le diagnostic croise la variation de prix, la popularité EDHREC et
les événements de marché déjà détectés (spoilers/réimpressions, bans, rumeurs Reddit).
"""
from datetime import date, timedelta

from database import market_data

POPULARITY_WINDOW_DAYS = 30
POPULARITY_RISE_PCT = 5.0
SPECULATIVE_RISE_PCT = 30.0
REPRINT_EVENTS = {"SPOILER_REPRINT", "REPRINT_RUMOR"}
BAN_EVENTS = {"BAN_RUMOR"}


def popularity_delta_pct(scryfall_id, today=None):
    """Évolution de la popularité EDHREC (positif = plus populaire) sur ~30 jours, ou None."""
    today = today or date.today()
    cutoff = today - timedelta(days=POPULARITY_WINDOW_DAYS)
    dated = []
    for item in market_data.get_edhrec_history(scryfall_id):
        try:
            dated.append((date.fromisoformat(item["snapshot_date"]), int(item["edhrec_rank"])))
        except (TypeError, ValueError, KeyError):
            continue
    dated = [(day, rank) for day, rank in dated if day >= cutoff and rank > 0]
    if len(dated) < 2 or dated[-1][0] == dated[0][0]:
        return None
    old_rank, new_rank = dated[0][1], dated[-1][1]
    return (old_rank - new_rank) / old_rank * 100


def popularity_badge(delta_pct):
    if delta_pct is None:
        return "⚪ n/d"
    if delta_pct > 0.5:
        return f"🟢 {delta_pct:+.0f} %"
    if delta_pct < -0.5:
        return f"🔴 {delta_pct:+.0f} %"
    return "🟡 0 %"


def _event_kinds(events):
    return {event.get("category") or event.get("event_type") for event in events}


def advise(change_pct, popularity_delta=None, events=()):
    """Retourne {kind, context, verdict} pour une variation de prix du stock."""
    events = list(events)
    kinds = _event_kinds(events)
    rising = change_pct > 0
    popular_up = popularity_delta is not None and popularity_delta >= POPULARITY_RISE_PCT
    popular_flat_or_down = popularity_delta is None or popularity_delta < POPULARITY_RISE_PCT
    popularity_text = (
        "popularité EDHREC non mesurée"
        if popularity_delta is None else f"popularité EDHREC {popularity_delta:+.0f} %"
    )

    if kinds & REPRINT_EVENTS:
        if rising:
            return {
                "kind": "REPRINT_RISK",
                "context": "Hausse alors qu'une réimpression est annoncée : le prix risque de s'effondrer.",
                "verdict": "Vente conseillée pour sécuriser la plus-value avant la réimpression.",
            }
        return {
            "kind": "REPRINT_DROP",
            "context": "Baisse suite à une réimpression annoncée ou rumorisée.",
            "verdict": "Éviter d'acheter ; vendre si le stock dépasse ton objectif de sortie.",
        }
    if kinds & BAN_EVENTS:
        return {
            "kind": "BAN_NEWS",
            "context": "Un ban/unban ou une rumeur de banlist concerne cette carte.",
            "verdict": "Vérifier l'annonce officielle avant toute décision.",
        }
    if rising and change_pct >= SPECULATIVE_RISE_PCT and popular_flat_or_down:
        return {
            "kind": "SPECULATIVE_BUBBLE",
            "context": f"Forte hausse ({change_pct:+.0f} %) sans demande organique ({popularity_text}) : "
                       "possible buyout artificiel.",
            "verdict": "Vente conseillée pour sécuriser la plus-value.",
        }
    if rising and popular_up:
        return {
            "kind": "HEALTHY_RISE",
            "context": f"Hausse liée à une forte adoption ({popularity_text}) : demande organique.",
            "verdict": "Conserver, tendance saine.",
        }
    if rising:
        return {
            "kind": "UNEXPLAINED_RISE",
            "context": f"Hausse sans signal de demande clair ({popularity_text}).",
            "verdict": "Surveiller ; envisager une vente partielle si la hausse se poursuit.",
        }
    if popular_up:
        return {
            "kind": "TEMPORARY_DROP",
            "context": f"Baisse malgré une popularité en hausse ({popularity_text}) : probablement temporaire.",
            "verdict": "Conserver et attendre le rebond.",
        }
    return {
        "kind": "UNEXPLAINED_DROP",
        "context": f"Baisse sans événement identifié ({popularity_text}).",
        "verdict": "Surveiller ; ne pas vendre dans la précipitation.",
    }


def advise_event(alert):
    """Verdict pour une alerte d'événement (spoiler, hype Reddit, ban) sur une carte du stock."""
    category = alert.get("category") or alert.get("event_type")
    if category in REPRINT_EVENTS:
        return {"verdict": "Envisager de vendre avant la sortie pour éviter la baisse liée à la réimpression."}
    if category in BAN_EVENTS:
        return {"verdict": "Vérifier l'annonce officielle ; un ban peut faire varier le prix dans les deux sens."}
    if category == "SPECULATIVE_PUMP":
        return {"verdict": "Prudence : ne pas acheter ; vendre si le prix monte sans demande réelle."}
    return {"verdict": "Signal communautaire à surveiller, pas une prévision de prix."}
