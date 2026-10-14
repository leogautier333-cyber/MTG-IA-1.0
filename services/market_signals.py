from datetime import date

from database import storage


def calculate_reprint_risk(scryfall_id, today=None):
    profile = storage.get_printing_profile(scryfall_id)
    if not profile:
        return {"status": "INSUFFICIENT_DATA", "score": None, "reason": "Impression absente du Bulk Scryfall."}

    if profile["reserved_list"]:
        return {
            **profile,
            "status": "LOW",
            "score": 5,
            "confidence": 0.9,
            "expected_change_30d_pct": None,
            "expected_change_90d_pct": None,
            "reason": "Impression Reserved List : le risque structurel de réimpression est faible.",
        }

    today = today or date.today()
    days_since_last_print = None
    if profile["last_release_at"]:
        try:
            days_since_last_print = (today - date.fromisoformat(profile["last_release_at"])).days
        except ValueError:
            pass

    score = min(35, max(0, profile["total_printings"] - 1) * 5)
    if days_since_last_print is not None:
        score += 30 if days_since_last_print < 365 else 20 if days_since_last_print < 1095 else 10 if days_since_last_print < 2555 else 0

    impacts_30 = [event["change_30d_pct"] for event in profile["reprint_events"] if event["change_30d_pct"] is not None]
    impacts_90 = [event["change_90d_pct"] for event in profile["reprint_events"] if event["change_90d_pct"] is not None]
    expected_30 = sum(impacts_30) / len(impacts_30) if impacts_30 else None
    expected_90 = sum(impacts_90) / len(impacts_90) if impacts_90 else None
    if expected_30 is not None and expected_30 < 0:
        score += min(25, round(abs(expected_30)))
    score = min(100, score)
    status = "HIGH" if score >= 65 else "MEDIUM" if score >= 35 else "LOW"
    historical_samples = max(len(impacts_30), len(impacts_90))
    confidence = min(0.95, 0.3 + historical_samples * 0.12) if historical_samples else 0.35
    return {
        **profile,
        "days_since_last_print": days_since_last_print,
        "historical_samples_30d": len(impacts_30),
        "historical_samples_90d": len(impacts_90),
        "expected_change_30d_pct": expected_30,
        "expected_change_90d_pct": expected_90,
        "status": status,
        "score": score,
        "confidence": confidence,
        "reason": (
            f"{profile['total_printings']} impression(s), dernière sortie il y a "
            f"{days_since_last_print if days_since_last_print is not None else 'un délai inconnu'} jour(s); "
            + (f"variation historique à 30 j : {expected_30:.1f} % (n={len(impacts_30)})." if expected_30 is not None else "pas encore d'échantillon post-réimpression à 30/90 jours.")
        ),
    }


def calculate_buyout_suspicion(price_change_pct, listing_change_pct=None,
                               seller_change_pct=None, price_vs_median_pct=None,
                               fundamental_cause=False, adoption_change_pct=None):
    available = [value for value in (listing_change_pct, seller_change_pct, price_vs_median_pct) if value is not None]
    if price_change_pct is None or len(available) < 2:
        return {
            "status": "INCONCLUSIVE",
            "score": None,
            "confidence": 0.0,
            "reason": "Il faut le mouvement de prix et au moins deux proxys d'offre Cardmarket vérifiables.",
        }
    if price_change_pct < 25:
        return {"status": "NO_SPIKE", "score": 0, "confidence": 0.8, "reason": "Hausse insuffisante pour caractériser un spike."}
    if fundamental_cause or (adoption_change_pct is not None and adoption_change_pct >= 20):
        return {"status": "FUNDAMENTAL_MOVE", "score": 0, "confidence": 0.75, "reason": "Une cause fondamentale ou une hausse d'adoption est déclarée."}

    score = 20
    score += 30 if listing_change_pct is not None and listing_change_pct <= -30 else 0
    score += 25 if seller_change_pct is not None and seller_change_pct <= -20 else 0
    score += 25 if price_vs_median_pct is not None and price_vs_median_pct >= 25 else 0
    status = "HIGH_SUSPICION" if score >= 75 else "SUSPICIOUS" if score >= 50 else "LOW_SUSPICION"
    completeness = len(available) / 3
    return {
        "status": status,
        "score": score,
        "confidence": round(0.4 + completeness * 0.2, 2),
        "reason": "Signal proxy basé sur l'évolution des annonces, vendeurs et écart à la médiane; ce n'est pas une preuve de manipulation.",
    }


def extract_market_events(posts, tracked_cards):
    event_rules = (
        ("REPRINT_RUMOR", ("reprint", "réimpression", "reprint rumor")),
        ("BAN_RUMOR", ("ban", "banni", "banned", "banlist")),
        ("DECK_TECH", ("deck tech", "top 8", "tournament", "tournoi", "edhrec")),
        ("SPECULATIVE_PUMP", ("buyout", "pump", "spike", "to the moon", "squeeze")),
    )
    events = {}
    for post in posts:
        text = f"{post.get('title', '')} {post.get('text', '')}".casefold()
        for card in tracked_cards:
            name = str(card.get("name", "")).casefold()
            if len(name) < 3 or name not in text:
                continue
            matching_type = next((
                event_type for event_type, terms in event_rules
                if any(term in text for term in terms)
            ), None)
            if not matching_type:
                continue
            source_url = post.get("url", "")
            key = (card.get("scryfall_id"), matching_type, source_url or text[:100])
            event = events.setdefault(key, {
                "event_type": matching_type,
                "scryfall_id": card.get("scryfall_id"),
                "card_name": card.get("name"),
                "source": "Reddit r/mtgfinance",
                "source_url": source_url,
                "confidence": 0.55 if source_url else 0.4,
                "evidence": post.get("title", ""),
            })
            event["confidence"] = min(0.9, event["confidence"] + 0.05)
    return list(events.values())