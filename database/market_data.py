"""Données de marché Scryfall : impressions (bulk), rang EDHREC, événements, profil de réimpression."""
import json
from datetime import date, datetime, timedelta, timezone
from itertools import islice

from database.storage import _database, _serialize, _store_alert


def _as_optional_price(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _as_optional_int(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def sync_bulk_prints(cards, synced_at=None):
    synced_at = synced_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    snapshot_day = str(synced_at)[:10]
    try:
        date.fromisoformat(snapshot_day)
    except ValueError:
        snapshot_day = datetime.now(timezone.utc).date().isoformat()
    summary = {"baseline": False, "new_cards": [], "reprints": [], "legality_changes": []}
    with _database() as connection:
        existing_count = connection.execute(
            "SELECT COUNT(*) AS count FROM bulk_prints"
        ).fetchone()["count"]
        summary["baseline"] = existing_count == 0
        connection.execute("CREATE TEMP TABLE seen_bulk_prints(scryfall_id TEXT PRIMARY KEY)")
        seen_count = 0
        iterator = iter(cards)
        while chunk := list(islice(iterator, 500)):
            chunk = [card for card in chunk if card.get("id")]
            if not chunk:
                continue
            ids = list(dict.fromkeys(card["id"] for card in chunk))
            placeholders = ", ".join("?" for _ in ids)
            existing = {
                row["scryfall_id"]: row for row in connection.execute(
                    f"""SELECT scryfall_id, oracle_id, legalities FROM bulk_prints
                        WHERE scryfall_id IN ({placeholders})""", ids
                ).fetchall()
            }
            oracle_ids = list({
                card["oracle_id"] for card in chunk
                if not summary["baseline"] and card["id"] not in existing and card.get("oracle_id")
            })
            tracked_oracles = set(oracle_ids)
            oracle_counts = {}
            for start in range(0, len(oracle_ids), 500):
                group = oracle_ids[start:start + 500]
                placeholders = ", ".join("?" for _ in group)
                for row in connection.execute(
                    f"""SELECT oracle_id, COUNT(*) AS count FROM bulk_prints
                        WHERE oracle_id IN ({placeholders}) GROUP BY oracle_id""", group
                ).fetchall():
                    oracle_counts[row["oracle_id"]] = row["count"]
            bulk_rows = []
            demand_rows = []
            seen_rows = []
            for card in chunk:
                scryfall_id = card["id"]
                oracle_id = card.get("oracle_id")
                legalities = card.get("legalities") or {}
                prices = card.get("prices") or {}
                edhrec_rank = _as_optional_int(card.get("edhrec_rank"))
                old = existing.get(scryfall_id)
                if old:
                    previous_legalities = json.loads(old["legalities"])
                    transitions = {
                        format_name: {"from": previous_legalities.get(format_name, "not_legal"), "to": status}
                        for format_name, status in legalities.items()
                        if previous_legalities.get(format_name, "not_legal") != status
                    }
                    if transitions:
                        event = {
                            "scryfall_id": scryfall_id,
                            "name": card.get("name", "Carte inconnue"),
                            "set_code": card.get("set", ""),
                            "transitions": transitions,
                        }
                        summary["legality_changes"].append(event)
                        _store_alert(
                            connection, f"legality:{scryfall_id}:{synced_at[:10]}",
                            "LEGALITY_CHANGE", scryfall_id, synced_at, event,
                        )
                elif not summary["baseline"]:
                    event = {
                        "scryfall_id": scryfall_id,
                        "oracle_id": oracle_id,
                        "name": card.get("name", "Carte inconnue"),
                        "set_code": card.get("set", ""),
                        "released_at": card.get("released_at"),
                    }
                    if oracle_id and oracle_counts.get(oracle_id, 0):
                        summary["reprints"].append(event)
                        _store_alert(
                            connection, f"reprint:{scryfall_id}",
                            "REPRINT", scryfall_id, synced_at, event,
                        )
                    else:
                        summary["new_cards"].append(event)
                        _store_alert(
                            connection, f"new-card:{scryfall_id}",
                            "NEW_CARD", scryfall_id, synced_at, event,
                        )
                if old and old["oracle_id"] in tracked_oracles:
                    oracle_counts[old["oracle_id"]] -= 1
                if oracle_id in tracked_oracles:
                    oracle_counts[oracle_id] = oracle_counts.get(oracle_id, 0) + 1
                bulk_rows.append((
                    scryfall_id, oracle_id, card.get("name", "Carte inconnue"),
                    card.get("set", ""), card.get("released_at"),
                    _serialize(legalities), int(bool(card.get("reserved"))),
                    _as_optional_price(prices.get("eur")),
                    _as_optional_price(prices.get("eur_foil")),
                    edhrec_rank,
                ))
                if edhrec_rank is not None:
                    demand_rows.append((scryfall_id, snapshot_day, edhrec_rank))
                seen_rows.append((scryfall_id,))

            connection.executemany(
                """INSERT INTO bulk_prints(
                       scryfall_id, oracle_id, name, set_code, released_at, legalities, reserved,
                       price_eur, price_eur_foil, edhrec_rank) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(scryfall_id) DO UPDATE SET oracle_id = excluded.oracle_id,
                       name = excluded.name, set_code = excluded.set_code,
                       released_at = excluded.released_at, legalities = excluded.legalities,
                       reserved = excluded.reserved, price_eur = excluded.price_eur,
                       price_eur_foil = excluded.price_eur_foil,
                       edhrec_rank = excluded.edhrec_rank""",
                bulk_rows,
            )
            if demand_rows:
                connection.executemany(
                    """INSERT INTO card_demand_snapshots(scryfall_id, snapshot_date, edhrec_rank)
                       VALUES (?, ?, ?)
                       ON CONFLICT(scryfall_id, snapshot_date)
                       DO UPDATE SET edhrec_rank = excluded.edhrec_rank""",
                    demand_rows,
                )
            connection.executemany("INSERT INTO seen_bulk_prints(scryfall_id) VALUES (?)", seen_rows)
            seen_count += len(seen_rows)
        if not seen_count or (existing_count and seen_count * 2 < existing_count):
            raise ValueError("Flux Bulk vide ou anormalement incomplet ; synchronisation annulée.")
        if not summary["baseline"]:
            connection.execute(
                "DELETE FROM bulk_prints WHERE scryfall_id NOT IN (SELECT scryfall_id FROM seen_bulk_prints)"
            )
    return summary


LEGALITY_KEY_PREFIX = "legalities:"


def get_legality_snapshots_with(connection, scryfall_ids):
    """Dernières légalités connues par impression ({scryfall_id: {format: statut}}), sans migration."""
    ids = list(dict.fromkeys(str(card_id) for card_id in scryfall_ids))
    snapshots = {}
    for start in range(0, len(ids), 500):
        keys = [LEGALITY_KEY_PREFIX + card_id for card_id in ids[start:start + 500]]
        placeholders = ", ".join("?" for _ in keys)
        for row in connection.execute(
            f"SELECT key, value FROM metadata WHERE key IN ({placeholders})", keys
        ).fetchall():
            try:
                value = json.loads(row["value"])
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                snapshots[row["key"][len(LEGALITY_KEY_PREFIX):]] = value
    return snapshots


def record_legality_snapshots_with(connection, snapshots):
    rows = [
        (LEGALITY_KEY_PREFIX + str(card_id), _serialize(legalities))
        for card_id, legalities in snapshots.items() if isinstance(legalities, dict) and legalities
    ]
    if rows:
        connection.executemany(
            """INSERT INTO metadata(key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            rows,
        )


def record_demand_snapshot(scryfall_id, edhrec_rank, snapshot_day=None):
    """Enregistre le rang EDHREC du jour pour une carte (sans dépendre du bulk Scryfall)."""
    rank = _as_optional_int(edhrec_rank)
    if rank is None or rank <= 0:
        return False
    snapshot_day = snapshot_day or datetime.now(timezone.utc).date().isoformat()
    with _database() as connection:
        connection.execute(
            """INSERT INTO card_demand_snapshots(scryfall_id, snapshot_date, edhrec_rank)
               VALUES (?, ?, ?)
               ON CONFLICT(scryfall_id, snapshot_date)
               DO UPDATE SET edhrec_rank = excluded.edhrec_rank""",
            (str(scryfall_id), snapshot_day, rank),
        )
    return True


def get_edhrec_history(scryfall_id):
    with _database() as connection:
        rows = connection.execute(
            """SELECT snapshot_date, edhrec_rank FROM card_demand_snapshots
               WHERE scryfall_id = ? ORDER BY snapshot_date""",
            (str(scryfall_id),),
        ).fetchall()
        if not rows:
            card = connection.execute(
                "SELECT edhrec_rank FROM bulk_prints WHERE scryfall_id = ?",
                (str(scryfall_id),),
            ).fetchone()
            if card and card["edhrec_rank"] is not None:
                return [{"snapshot_date": None, "edhrec_rank": card["edhrec_rank"]}]
            return []
    return [dict(row) for row in rows]


def get_edhrec_histories(scryfall_ids):
    ids = list(dict.fromkeys(str(card_id) for card_id in scryfall_ids))
    if not ids:
        return {}
    with _database() as connection:
        return get_edhrec_histories_with(connection, ids)


def get_edhrec_histories_with(connection, ids):
    histories = {card_id: [] for card_id in ids}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        placeholders = ", ".join("?" for _ in chunk)
        rows = connection.execute(
            f"""SELECT scryfall_id, snapshot_date, edhrec_rank
                FROM card_demand_snapshots WHERE scryfall_id IN ({placeholders})
                ORDER BY snapshot_date""",
            chunk,
        ).fetchall()
        for row in rows:
            histories[row["scryfall_id"]].append({
                "snapshot_date": row["snapshot_date"], "edhrec_rank": row["edhrec_rank"],
            })
        missing = [card_id for card_id in chunk if not histories[card_id]]
        if missing:
            placeholders = ", ".join("?" for _ in missing)
            rows = connection.execute(
                f"SELECT scryfall_id, edhrec_rank FROM bulk_prints WHERE scryfall_id IN ({placeholders})",
                missing,
            ).fetchall()
            for row in rows:
                if row["edhrec_rank"] is not None:
                    histories[row["scryfall_id"]].append({
                        "snapshot_date": None, "edhrec_rank": row["edhrec_rank"],
                    })
    return histories


def record_demand_snapshots_with(connection, records, snapshot_day):
    values = []
    for card_id, value in records.items():
        rank = _as_optional_int(value)
        if rank is not None and rank > 0:
            values.append((str(card_id), snapshot_day, rank))
    if values:
        connection.executemany(
            """INSERT INTO card_demand_snapshots(scryfall_id, snapshot_date, edhrec_rank)
               VALUES (?, ?, ?)
               ON CONFLICT(scryfall_id, snapshot_date)
               DO UPDATE SET edhrec_rank = excluded.edhrec_rank""",
            values,
        )


def get_recent_market_events(scryfall_id, since_date):
    with _database() as connection:
        card = connection.execute(
            "SELECT oracle_id FROM bulk_prints WHERE scryfall_id = ?",
            (str(scryfall_id),),
        ).fetchone()
        oracle_id = card["oracle_id"] if card else None
        rows = connection.execute(
            """SELECT alert_id, signal_type, scryfall_id, created_at, payload
               FROM alerts
               WHERE signal_type IN ('REPRINT', 'LEGALITY_CHANGE') AND created_at >= ?
               ORDER BY created_at""",
            (str(since_date),),
        ).fetchall()
    events = []
    for row in rows:
        payload = json.loads(row["payload"])
        same_print = row["scryfall_id"] == str(scryfall_id)
        same_oracle = bool(oracle_id and payload.get("oracle_id") == oracle_id)
        if same_print or (row["signal_type"] == "REPRINT" and same_oracle):
            events.append({
                "alert_id": row["alert_id"],
                "signal_type": row["signal_type"],
                "scryfall_id": row["scryfall_id"],
                "created_at": row["created_at"],
                "payload": payload,
            })
    return events


def get_printing_profile(scryfall_id):
    with _database() as connection:
        selected = connection.execute(
            "SELECT * FROM bulk_prints WHERE scryfall_id = ?", (str(scryfall_id),)
        ).fetchone()
        if not selected:
            return None
        if selected["oracle_id"]:
            printings = connection.execute(
                """SELECT scryfall_id, released_at, reserved FROM bulk_prints
                   WHERE oracle_id = ? ORDER BY released_at""",
                (selected["oracle_id"],),
            ).fetchall()
        else:
            printings = [selected]
        alerts = connection.execute(
            "SELECT created_at, payload FROM alerts WHERE signal_type = 'REPRINT' ORDER BY created_at"
        ).fetchall()
        price_series = {}
        for printing in printings:
            rows = connection.execute(
                """SELECT snapshot_date, AVG(price_eur) AS price FROM price_snapshots
                   WHERE scryfall_id = ? AND foil = 0 GROUP BY snapshot_date ORDER BY snapshot_date""",
                (printing["scryfall_id"],),
            ).fetchall()
            price_series[printing["scryfall_id"]] = [
                (row["snapshot_date"], row["price"]) for row in rows
            ]

    reprint_events = []
    for alert in alerts:
        payload = json.loads(alert["payload"])
        if not selected["oracle_id"] or payload.get("oracle_id") != selected["oracle_id"]:
            continue
        event_day = str(alert["created_at"])[:10]
        event_impacts = {30: [], 90: []}
        for printing in printings:
            print_id = printing["scryfall_id"]
            if print_id == payload.get("scryfall_id"):
                continue
            points = price_series.get(print_id, [])
            before = [(day, price) for day, price in points if day <= event_day]
            if not before:
                continue
            base_price = before[-1][1]
            if base_price <= 0:
                continue
            for horizon in (30, 90):
                target_day = (date.fromisoformat(event_day) + timedelta(days=horizon)).isoformat()
                after = next(((day, price) for day, price in points if day >= target_day), None)
                if after:
                    event_impacts[horizon].append((after[1] - base_price) / base_price * 100)
        reprint_events.append({
            "scryfall_id": payload.get("scryfall_id"),
            "event_date": event_day,
            "change_30d_pct": sum(event_impacts[30]) / len(event_impacts[30]) if event_impacts[30] else None,
            "change_90d_pct": sum(event_impacts[90]) / len(event_impacts[90]) if event_impacts[90] else None,
        })

    released_dates = sorted({
        printing["released_at"] for printing in printings if printing["released_at"]
    })
    release_intervals_days = []
    for previous_release, current_release in zip(released_dates, released_dates[1:]):
        try:
            release_intervals_days.append(
                (date.fromisoformat(current_release) - date.fromisoformat(previous_release)).days
            )
        except ValueError:
            continue
    return {
        "scryfall_id": selected["scryfall_id"],
        "oracle_id": selected["oracle_id"],
        "name": selected["name"],
        "reserved_list": any(bool(printing["reserved"]) for printing in printings),
        "total_printings": len(printings),
        "release_dates": released_dates,
        "release_intervals_days": release_intervals_days,
        "average_release_interval_days": (
            sum(release_intervals_days) / len(release_intervals_days)
            if release_intervals_days else None
        ),
        "last_release_at": max(released_dates) if released_dates else None,
        "reprint_events": reprint_events,
    }


def get_alternative_printings(scryfall_id):
    with _database() as connection:
        selected = connection.execute(
            "SELECT oracle_id FROM bulk_prints WHERE scryfall_id = ?", (str(scryfall_id),)
        ).fetchone()
        if not selected or not selected["oracle_id"]:
            return []
        rows = connection.execute(
            """SELECT scryfall_id, name, set_code, released_at, reserved,
                      price_eur, price_eur_foil
               FROM bulk_prints WHERE oracle_id = ? AND scryfall_id != ?
               ORDER BY released_at DESC""",
            (selected["oracle_id"], str(scryfall_id)),
        ).fetchall()
    return [dict(row) for row in rows]
