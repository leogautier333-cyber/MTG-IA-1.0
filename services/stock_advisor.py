"""Règles métier : explique une variation de prix d'une carte du stock et donne un verdict d'action.

Aucun appel externe : le diagnostic croise la variation de prix, la popularité EDHREC et
les événements de marché déjà détectés (réimpressions annoncées par Scryfall).
"""
from datetime import date, timedelta

from database import market_data

POPULARITY_WINDOW_DAYS = 30
POPULARITY_RISE_PCT = 5.0
SPECULATIVE_RISE_PCT = 30.0
REPRINT_EVENTS = {"SPOILER_REPRINT"}
LEGALITY_EVENTS = {"LEGALITY_CHANGE"}
BANNED_STATUSES = {"banned", "restricted"}


def popularity_delta_pct(scryfall_id, today=None):
    """Évolution de la popularité EDHREC (positif = plus populaire) sur ~30 jours, ou None."""
    return popularity_delta_from_history(market_data.get_edhrec_history(scryfall_id), today)


def popularity_deltas_pct(scryfall_ids, today=None):
    histories = market_data.get_edhrec_histories(scryfall_ids)
    return {card_id: popularity_delta_from_history(history, today)
            for card_id, history in histories.items()}


def popularity_delta_from_history(history, today=None):
    today = today or date.today()
    cutoff = today - timedelta(days=POPULARITY_WINDOW_DAYS)
    dated = []
    for item in history:
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
                "context": "Hausse observée malgré un signal de réimpression annoncée ou supposée.",
                "verdict": "Vérifier la source et évaluer le risque avant toute décision.",
            }
        return {
            "kind": "REPRINT_DROP",
            "context": "Baisse observée en présence d'un signal de réimpression, sans causalité établie.",
            "verdict": "Vérifier la source et évaluer le risque avant toute décision.",
        }
    if kinds & LEGALITY_EVENTS:
        return {
            "kind": "LEGALITY_NEWS",
            "context": "Un changement de légalité (ban, restriction ou retour) concerne cette carte.",
            "verdict": "Vérifier l'annonce officielle avant toute décision.",
        }
    if rising and change_pct >= SPECULATIVE_RISE_PCT and popular_flat_or_down:
        return {
            "kind": "SPECULATIVE_BUBBLE",
            "context": f"Forte hausse ({change_pct:+.0f} %) sans progression mesurée du rang "
                       f"({popularity_text}) : une anomalie possible, pas une preuve de manipulation.",
            "verdict": "Vérifier les sources et envisager les options sans présumer de la tendance.",
        }
    if rising and popular_up:
        return {
            "kind": "ORGANIC_RISE",
            "context": f"Hausse de {change_pct:+.0f} % soutenue par la demande : "
                       f"rang EDHREC en amélioration ({popularity_text}).",
            "verdict": "Hausse organique probable : surveiller, les causes et l'évolution future restent incertaines.",
        }
    if rising and popularity_delta is not None:
        return {
            "kind": "ISOLATED_SPIKE",
            "context": f"Pic de prix ({change_pct:+.0f} %) sans amélioration du rang EDHREC ({popularity_text}) : "
                       "rien ne le soutient dans la demande mesurée.",
            "verdict": "Pic isolé : ne pas extrapoler ; envisager de vendre une partie si le prix tient.",
        }
    if rising:
        return {
            "kind": "UNEXPLAINED_RISE",
            "context": f"Hausse sans signal de demande mesurable ({popularity_text}).",
            "verdict": "Surveiller ; envisager une vente partielle si la hausse se poursuit.",
        }
    if popular_up:
        return {
            "kind": "TEMPORARY_DROP",
            "context": f"Baisse malgré une popularité en hausse ({popularity_text}) ; durée inconnue.",
            "verdict": "Surveiller : un rebond n'est pas garanti.",
        }
    return {
        "kind": "UNEXPLAINED_DROP",
        "context": f"Baisse sans événement identifié ({popularity_text}).",
        "verdict": "Surveiller ; ne pas vendre dans la précipitation.",
    }


def advise_event(alert):
    """Verdict pour une alerte d'événement (réimpression annoncée) sur une carte du stock."""
    category = alert.get("category") or alert.get("event_type")
    if category in REPRINT_EVENTS:
        return {"verdict": "Vérifier la réimpression et évaluer le risque avant d'envisager de vendre."}
    if category in LEGALITY_EVENTS:
        targets = [change.get("to") for change in (alert.get("transitions") or {}).values()]
        if any(status in BANNED_STATUSES for status in targets):
            return {"verdict": "Ban ou restriction : vérifier l'annonce officielle ; le prix peut réagir dans les deux sens."}
        if any(status == "legal" for status in targets):
            return {"verdict": "Retour en légalité : la demande peut remonter, vérifier l'annonce officielle."}
        return {"verdict": "Changement de légalité : vérifier le format concerné avant toute décision."}
    return {"verdict": "Événement à surveiller, pas une prévision de prix."}
