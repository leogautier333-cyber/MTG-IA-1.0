import hashlib
from datetime import date, datetime, timedelta, timezone

from database import storage


def calculate_reprint_risk(scryfall_id, today=None):
    """Estimate reprint exposure from Reserved List, release recency and release cadence."""
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

    printings_count = max(profile["total_printings"], len(profile.get("release_dates", [])))
    recurrence_score = min(24, max(0, printings_count - 1) * 4)
    recency_score = 0
    if days_since_last_print is not None:
        recency_score = (
            35 if days_since_last_print < 365 else
            25 if days_since_last_print < 1095 else
            15 if days_since_last_print < 2555 else
            8 if days_since_last_print < 3650 else 0
        )
    average_interval = profile.get("average_release_interval_days")
    cadence_score = (
        30 if average_interval is not None and average_interval <= 365 else
        24 if average_interval is not None and average_interval <= 730 else
        16 if average_interval is not None and average_interval <= 1460 else
        8 if average_interval is not None and average_interval <= 2920 else 0
    )
    score = recurrence_score + recency_score + cadence_score

    impacts_30 = [event["change_30d_pct"] for event in profile["reprint_events"] if event["change_30d_pct"] is not None]
    impacts_90 = [event["change_90d_pct"] for event in profile["reprint_events"] if event["change_90d_pct"] is not None]
    expected_30 = sum(impacts_30) / len(impacts_30) if impacts_30 else None
    expected_90 = sum(impacts_90) / len(impacts_90) if impacts_90 else None
    if expected_30 is not None and expected_30 < 0:
        score += min(25, round(abs(expected_30)))
    score = min(100, score)
    status = "HIGH" if score >= 65 else "MEDIUM" if score >= 35 else "LOW"
    historical_samples = max(len(impacts_30), len(impacts_90))
    confidence = min(0.95, 0.35 + len(profile.get("release_intervals_days", [])) * 0.08 + historical_samples * 0.12)
    cadence_label = (
        f"intervalle moyen de {average_interval:.0f} jours entre sorties"
        if average_interval is not None else "historique de cadence trop court"
    )
    return {
        **profile,
        "days_since_last_print": days_since_last_print,
        "recurrence_score": recurrence_score,
        "recency_score": recency_score,
        "cadence_score": cadence_score,
        "historical_samples_30d": len(impacts_30),
        "historical_samples_90d": len(impacts_90),
        "expected_change_30d_pct": expected_30,
        "expected_change_90d_pct": expected_90,
        "status": status,
        "score": score,
        "confidence": confidence,
        "reason": (
            f"{profile['total_printings']} impression(s), dernière sortie il y a "
            f"{days_since_last_print if days_since_last_print is not None else 'un délai inconnu'} jour(s), "
            f"{cadence_label}; "
            + (f"variation historique à 30 j : {expected_30:.1f} % (n={len(impacts_30)})." if expected_30 is not None else "pas encore d'échantillon post-réimpression à 30/90 jours.")
        ),
    }


def analyze_snapshot_buyout(scryfall_id, foil=False, language="FR", condition="NM",
                            window_days=7, spike_threshold_pct=30.0, today=None):
    """Score unexplained price spikes using daily snapshots, Scryfall events and EDHREC rank."""
    points = storage.get_price_points(scryfall_id, foil, language, condition)
    normalized_points = []
    for snapshot_day, price in points:
        try:
            price = float(price)
            parsed_day = date.fromisoformat(snapshot_day)
        except (TypeError, ValueError):
            continue
        if price > 0:
            normalized_points.append((parsed_day, price))
    if len(normalized_points) < 2:
        return {
            "status": "INSUFFICIENT_DATA", "score": None, "confidence": 0.0,
            "reason": "Deux snapshots journaliers minimum sont requis.",
        }

    latest_day, current_price = normalized_points[-1]
    today = today or date.today()
    snapshot_age_days = (today - latest_day).days
    if snapshot_age_days > 3:
        return {
            "status": "STALE_DATA", "score": None, "confidence": 0.0,
            "observed_date": latest_day.isoformat(),
            "reason": f"Dernier snapshot vieux de {snapshot_age_days} jours; actualise les données avant d'évaluer un spike.",
        }

    demand_history = storage.get_edhrec_history(scryfall_id)
    dated_demand = [
        item for item in demand_history
        if item.get("snapshot_date") and item.get("edhrec_rank") is not None
        and date.fromisoformat(item["snapshot_date"]) <= latest_day
    ]
    latest_rank = dated_demand[-1]["edhrec_rank"] if dated_demand else None
    if latest_rank is None and demand_history and demand_history[-1].get("snapshot_date") is None:
        latest_rank = demand_history[-1].get("edhrec_rank")

    cutoff_day = today - timedelta(days=window_days)
    baseline = next(
        ((snapshot_day, price) for snapshot_day, price in reversed(normalized_points)
         if snapshot_day <= cutoff_day),
        None,
    )
    if baseline is None:
        return {
            "status": "INSUFFICIENT_DATA", "score": None, "confidence": 0.0,
            "reason": f"Il manque un snapshot de référence vieux d'au moins {window_days} jours.",
        }

    baseline_day, baseline_price = baseline
    change_pct = (current_price - baseline_price) / baseline_price * 100
    result = {
        "scryfall_id": scryfall_id,
        "window_days": window_days,
        "baseline_date": baseline_day.isoformat(),
        "observed_date": latest_day.isoformat(),
        "baseline_price": baseline_price,
        "current_price": current_price,
        "price_change_pct": change_pct,
        "edhrec_rank": latest_rank,
    }
    if change_pct < spike_threshold_pct:
        return {
            **result,
            "status": "NO_SPIKE",
            "score": 0,
            "confidence": 0.8,
            "reason": f"Variation de {change_pct:.1f} % sur {window_days} jours, sous le seuil de {spike_threshold_pct:.0f} %.",
        }

    events = storage.get_recent_market_events(scryfall_id, baseline_day.isoformat())
    if events:
        event_types = sorted({event["signal_type"] for event in events})
        return {
            **result,
            "status": "FUNDAMENTAL_EVENT",
            "score": 0,
            "confidence": 0.9,
            "fundamental_events": events,
            "reason": "Spike associé à un changement Scryfall récent : " + ", ".join(event_types) + ".",
        }

    previous_returns = []
    for (previous_day, previous_price), (next_day, next_price) in zip(
        normalized_points, normalized_points[1:]
    ):
        if next_day >= baseline_day or previous_price <= 0:
            continue
        daily_return = (next_price - previous_price) / previous_price * 100
        days_between = max(1, (next_day - previous_day).days)
        previous_returns.append(daily_return / days_between)
    median_daily_change = 0.0
    robust_z_score = None
    if len(previous_returns) >= 3:
        sorted_returns = sorted(previous_returns)
        median_daily_change = sorted_returns[len(sorted_returns) // 2]
        deviations = sorted(abs(value - median_daily_change) for value in previous_returns)
        median_deviation = deviations[len(deviations) // 2]
        robust_scale = max(1.0, 1.4826 * median_deviation * (window_days ** 0.5))
        expected_change = median_daily_change * window_days
        robust_z_score = (change_pct - expected_change) / robust_scale

    score = min(100, round(35 + max(0, change_pct - spike_threshold_pct) * 1.25))
    if robust_z_score is not None:
        score = min(100, score + max(0, round((robust_z_score - 2) * 5)))

    demand_reference_day = latest_day - timedelta(days=30)
    old_ranks = [
        item for item in dated_demand
        if date.fromisoformat(item["snapshot_date"]) <= demand_reference_day
    ]
    adoption_change_pct = None
    if old_ranks and latest_rank and old_ranks[-1]["edhrec_rank"] > 0:
        old_rank = old_ranks[-1]["edhrec_rank"]
        adoption_change_pct = (old_rank - latest_rank) / old_rank * 100
    demand_supported = adoption_change_pct is not None and adoption_change_pct >= 20
    popular_context = latest_rank is not None and latest_rank <= 1000
    if demand_supported:
        score = max(0, score - 40)
        status = "DEMAND_SUPPORTED"
    elif popular_context:
        score = max(0, score - 15)
        status = "POPULARITY_CONTEXT"
    else:
        status = "HIGH_SUSPICION" if score >= 70 else "SUSPICIOUS"

    confidence = min(0.9, 0.4 + min(len(previous_returns), 6) * 0.05)
    if len(dated_demand) >= 2:
        confidence = min(0.95, confidence + 0.1)
    reason = (
        f"Hausse de {change_pct:.1f} % en {window_days} jours sans réimpression ni changement de légalité détecté. "
    )
    if demand_supported:
        reason += f"Le rang EDHREC s'est amélioré de {adoption_change_pct:.1f} % en environ 30 jours : hausse soutenue par l'adoption."
    elif popular_context:
        reason += f"Rang EDHREC actuel {latest_rank} : contexte Commander populaire, mais tendance insuffisante pour confirmer une hausse d'adoption."
    else:
        reason += "Aucune amélioration récente du rang EDHREC ne confirme une demande accrue."
    return {
        **result,
        "status": status,
        "score": score,
        "confidence": confidence,
        "robust_z_score": robust_z_score,
        "baseline_daily_change_pct": median_daily_change,
        "edhrec_rank": latest_rank,
        "edhrec_improvement_pct_30d": adoption_change_pct,
        "demand_supported": demand_supported,
        "fundamental_events": [],
        "reason": reason,
    }


def scan_snapshot_buyouts(cards, minimum_score=60, today=None):
    findings = []
    now = datetime.now(timezone.utc)
    for card in cards:
        scryfall_id = card.get("scryfall_id")
        if not scryfall_id:
            continue
        signal = analyze_snapshot_buyout(
            scryfall_id,
            foil=card.get("foil", False),
            language=card.get("language", "FR"),
            condition=card.get("condition", "NM"),
            today=today,
        )
        if signal.get("score") is None or signal["score"] < minimum_score:
            continue
        if signal["status"] not in {"SUSPICIOUS", "HIGH_SUSPICION"}:
            continue
        variant = (
            scryfall_id, bool(card.get("foil")), card.get("language", "FR"),
            card.get("condition", "NM"), signal["observed_date"],
        )
        alert_id = "anti-buyout:" + hashlib.sha256(repr(variant).encode("utf-8")).hexdigest()
        alert = {
            **signal,
            "card_name": card.get("name", "Carte inconnue"),
            "foil": bool(card.get("foil")),
            "language": card.get("language", "FR"),
            "condition": card.get("condition", "NM"),
        }
        storage.record_alert(alert_id, "ANTI_BUYOUT", scryfall_id, now.isoformat(timespec="seconds"), alert)
        findings.append({"alert_id": alert_id, **alert})
    return findings


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