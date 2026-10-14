from datetime import date, datetime, timedelta, timezone

from database import storage


def _parse_date(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except (AttributeError, ValueError):
        return date.fromisoformat(str(value)[:10])


def evaluate_alerts(now=None, horizons=(7, 30)):
    now = now or datetime.now(timezone.utc)
    evaluated = 0
    for alert in storage.get_alerts_for_evaluation():
        payload = alert["payload"]
        reference_price = payload.get("reference_price")
        card_id = alert.get("scryfall_id")
        if not card_id or reference_price in (None, 0, ""):
            continue
        try:
            reference_price = float(reference_price)
            created_date = _parse_date(alert["created_at"])
        except (TypeError, ValueError):
            continue
        points = storage.get_price_points(
            card_id, payload.get("foil", False), payload.get("language", "FR"),
            payload.get("condition", "NM"),
        )
        for horizon in horizons:
            target_date = created_date + timedelta(days=horizon)
            if now.date() < target_date:
                continue
            observation = next(
                ((day, price) for day, price in points
                 if target_date <= date.fromisoformat(day) <= target_date + timedelta(days=3)),
                None,
            )
            if observation is None:
                continue
            observed_date, observed_price = observation
            change_pct = (float(observed_price) - reference_price) / reference_price * 100
            expects_rise = alert["signal_type"] in {"BUY_SIGNAL", "SELL_RECOMMENDATION", "PRICE_DROP"}
            direction_correct = change_pct > 0 if expects_rise else change_pct < 0
            storage.record_alert_evaluation(
                alert["alert_id"], horizon, target_date.isoformat(), observed_date,
                reference_price, observed_price, change_pct, direction_correct,
                evaluated_at=now.isoformat(timespec="seconds"),
            )
            evaluated += 1
    return {"evaluations_written": evaluated, "metrics": storage.get_alert_metrics()}


def backtest_price_threshold(scryfall_id, threshold_pct=15.0, horizon_days=7,
                             direction="drop", min_absolute_change=0.0,
                             foil=False, language="FR", condition="NM"):
    if direction not in {"drop", "spike"}:
        raise ValueError("direction doit valoir 'drop' ou 'spike'.")
    threshold_pct = abs(float(threshold_pct))
    horizon_days = int(horizon_days)
    if horizon_days < 1:
        raise ValueError("horizon_days doit être supérieur ou égal à 1.")

    points = storage.get_price_points(scryfall_id, foil, language, condition)
    samples = []
    for index in range(1, len(points)):
        previous_point = points[index - 1]
        previous_price = previous_point[1]
        signal_date, signal_price = points[index]
        if previous_price <= 0:
            continue
        change_pct = (signal_price - previous_price) / previous_price * 100
        threshold_hit = change_pct <= -threshold_pct if direction == "drop" else change_pct >= threshold_pct
        if not threshold_hit or abs(signal_price - previous_price) < min_absolute_change:
            continue
        target_date = date.fromisoformat(signal_date) + timedelta(days=horizon_days)
        outcome = next(
            ((day, price) for day, price in points[index + 1:]
             if target_date <= date.fromisoformat(day) <= target_date + timedelta(days=3)),
            None,
        )
        if outcome is None:
            continue
        outcome_date, outcome_price = outcome
        outcome_pct = (outcome_price - signal_price) / signal_price * 100 if signal_price else 0.0
        correct = outcome_pct > 0 if direction == "drop" else outcome_pct < 0
        samples.append({
            "signal_date": signal_date,
            "outcome_date": outcome_date,
            "signal_change_pct": change_pct,
            "outcome_change_pct": outcome_pct,
            "direction_correct": correct,
        })
    count = len(samples)
    return {
        "scryfall_id": scryfall_id,
        "direction": direction,
        "threshold_pct": threshold_pct,
        "horizon_days": horizon_days,
        "sample_count": count,
        "accuracy": sum(sample["direction_correct"] for sample in samples) / count if count else None,
        "samples": samples,
    }