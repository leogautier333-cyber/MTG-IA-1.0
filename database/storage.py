import hashlib
import json
import os
import sqlite3
import threading
from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit


PROJECT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DATABASE_PATH = Path(os.getenv("MTG_DATABASE_PATH", PROJECT_DATA_DIR / "mtg_manager.sqlite3"))
DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("MTG_DATABASE_URL")
_INITIALIZED_PATHS = set()
_INITIALIZATION_LOCK = threading.Lock()

REQUIRED_TABLES = frozenset({
    "metadata", "inventory", "watchlist", "price_snapshots", "source_health",
    "alerts", "alert_evaluations", "bulk_prints", "card_demand_snapshots",
})


class DatabaseConfigurationError(RuntimeError):
    """Raised when a requested database backend is missing or misconfigured."""


class DatabaseConnectionError(RuntimeError):
    """Raised when a configured database cannot be reached or initialized."""


def _is_github_actions():
    return os.getenv("GITHUB_ACTIONS", "").lower() == "true"


def _configured_database_url():
    return os.getenv("DATABASE_URL") or os.getenv("MTG_DATABASE_URL") or DATABASE_URL


def get_database_backend():
    if _configured_database_url():
        return "postgres"
    if _is_github_actions():
        raise DatabaseConfigurationError(
            "DATABASE_URL is required in GitHub Actions; refusing to use ephemeral SQLite. "
            "Configure a PostgreSQL URI as the DATABASE_URL Actions secret."
        )
    return "sqlite"


class _DatabaseConnection:
    def __init__(self, connection, dialect):
        self.connection = connection
        self.dialect = dialect

    def execute(self, query, parameters=()):
        if self.dialect == "postgres":
            query = query.replace("?", "%s")
        return self.connection.execute(query, parameters)

    def executemany(self, query, parameters):
        if self.dialect == "postgres":
            query = query.replace("?", "%s")
            cursor = self.connection.cursor()
            cursor.executemany(query, parameters)
            return cursor
        return self.connection.executemany(query, parameters)

    def executescript(self, script):
        if self.dialect == "sqlite":
            return self.connection.executescript(script)
        for statement in script.split(";"):
            statement = statement.strip()
            if statement:
                self.execute(statement)

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()


def _connect():
    database_url = _configured_database_url()
    if database_url:
        try:
            parsed_url = urlsplit(database_url)
            if parsed_url.scheme not in {"postgres", "postgresql"} or not parsed_url.hostname or not parsed_url.path.strip("/"):
                raise DatabaseConfigurationError(
                    "DATABASE_URL must be a PostgreSQL URI containing a host and database name."
                )
        except ValueError:
            raise DatabaseConfigurationError(
                "DATABASE_URL is not a valid PostgreSQL URI; check its encoding and port."
            ) from None

        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError:
            raise DatabaseConfigurationError(
                "PostgreSQL is configured but psycopg is missing; install requirements.txt."
            ) from None

        try:
            connection = psycopg.connect(
                database_url,
                row_factory=dict_row,
                connect_timeout=15,
                application_name="magic-trading-profit-manager",
                sslmode=os.getenv("DATABASE_SSLMODE", "require"),
            )
        except Exception as error:
            error_type = type(error).__name__
            raise DatabaseConnectionError(
                f"PostgreSQL connection failed ({error_type}) after a 15-second timeout. "
                "Check the database host, network allow-list, credentials and TLS settings; "
                "the connection string is intentionally omitted from this error."
            ) from None
        return _DatabaseConnection(connection, "postgres")

    if _is_github_actions():
        raise DatabaseConfigurationError(
            "DATABASE_URL is required in GitHub Actions; refusing to use ephemeral SQLite. "
            "Configure a PostgreSQL URI as the DATABASE_URL Actions secret."
        )

    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return _DatabaseConnection(connection, "sqlite")


def _read_json(path, default):
    try:
        with path.open("r", encoding="utf-8") as source:
            return json.load(source)
    except (OSError, json.JSONDecodeError):
        return default


def _serialize(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _migrate_legacy_data(connection):
    migrated = connection.execute(
        "SELECT value FROM metadata WHERE key = 'legacy_json_migrated'"
    ).fetchone()
    legacy_dir = DATABASE_PATH.parent
    if not migrated:
        collection = _read_json(legacy_dir / "collection.json", [])
        watchlist = _read_json(legacy_dir / "watchlist.json", [])

        if isinstance(collection, list):
            _replace_inventory(connection, collection)
        if isinstance(watchlist, list):
            _replace_watchlist(connection, watchlist)
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES ('legacy_json_migrated', '1')"
        )

    price_history_path = legacy_dir / "price_history.json"
    try:
        digest = hashlib.sha256()
        file_size = 0
        with price_history_path.open("rb") as history_file:
            while chunk := history_file.read(1024 * 1024):
                digest.update(chunk)
                file_size += len(chunk)
        price_history_fingerprint = f"{file_size}:{digest.hexdigest()}"
    except OSError:
        price_history_fingerprint = "missing"
    previous_fingerprint = connection.execute(
        "SELECT value FROM metadata WHERE key = 'legacy_price_history_fingerprint'"
    ).fetchone()
    if not previous_fingerprint or previous_fingerprint["value"] != price_history_fingerprint:
        _import_legacy_price_history(connection, price_history_path)
        connection.execute(
            """INSERT INTO metadata(key, value) VALUES ('legacy_price_history_fingerprint', ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (price_history_fingerprint,),
        )

    watchlist_history_migrated = connection.execute(
        "SELECT value FROM metadata WHERE key = 'legacy_watchlist_history_migrated'"
    ).fetchone()
    if not watchlist_history_migrated:
        watchlist_state = _read_json(legacy_dir / "nexus_market_state.json", {})
        for scryfall_id, observations in (watchlist_state.get("cards", {}) if isinstance(watchlist_state, dict) else {}).items():
            for observation in observations if isinstance(observations, list) else []:
                observed_at = observation.get("observed_at", "")
                snapshot_day = str(observed_at)[:10]
                try:
                    date.fromisoformat(snapshot_day)
                    price = float(observation["price_eur"])
                except (KeyError, TypeError, ValueError):
                    continue
                _insert_snapshot(
                    connection, scryfall_id, False, "FR", "NM",
                    snapshot_day, observed_at, price,
                )
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES ('legacy_watchlist_history_migrated', '1')"
        )

    legacy_alerts_migrated = connection.execute(
        "SELECT value FROM metadata WHERE key = 'legacy_ai_alerts_migrated'"
    ).fetchone()
    if not legacy_alerts_migrated:
        predictions = _read_json(legacy_dir / "ai_predictions.json", [])
        for index, prediction in enumerate(predictions if isinstance(predictions, list) else []):
            if not isinstance(prediction, dict):
                continue
            _store_alert(
                connection,
                prediction.get("prediction_id", f"legacy-prediction-{index}"),
                prediction.get("category", "LEGACY_AI_ALERT"),
                prediction.get("scryfall_id"),
                prediction.get("timestamp", datetime.now(timezone.utc).isoformat(timespec="seconds")),
                prediction,
            )
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES ('legacy_ai_alerts_migrated', '1')"
        )
    connection.commit()


def _import_legacy_price_history(connection, path):
    price_history = _read_json(path, {})
    if not isinstance(price_history, dict):
        return
    for scryfall_id, points in price_history.items():
        if not isinstance(points, dict):
            continue
        for timestamp, prices in points.items():
            if not isinstance(prices, dict):
                continue
            snapshot_day = str(timestamp)[:10]
            try:
                date.fromisoformat(snapshot_day)
            except ValueError:
                continue
            for foil, price_key in ((False, "eur"), (True, "eur_foil")):
                price = prices.get(price_key)
                if price is None:
                    continue
                try:
                    _insert_snapshot(
                        connection, scryfall_id, foil, "FR", "NM",
                        snapshot_day, str(timestamp), float(price),
                    )
                except (TypeError, ValueError):
                    continue


def initialize_database():
    database_url = _configured_database_url()
    if not database_url and _is_github_actions():
        raise DatabaseConfigurationError(
            "DATABASE_URL is required in GitHub Actions; refusing to initialize an ephemeral SQLite database."
        )
    database_path = database_url or str(DATABASE_PATH.resolve())
    if database_path in _INITIALIZED_PATHS:
        return
    with _INITIALIZATION_LOCK:
        if database_path in _INITIALIZED_PATHS:
            return
        with closing(_connect()) as connection:
            schema = """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS inventory (
                entry_id INTEGER PRIMARY KEY,
                scryfall_id TEXT NOT NULL,
                foil INTEGER NOT NULL DEFAULT 0,
                language TEXT NOT NULL DEFAULT 'FR',
                condition TEXT NOT NULL DEFAULT 'NM',
                set_code TEXT NOT NULL DEFAULT '',
                collector_number TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'FOR_SALE',
                date_added TEXT,
                date_sold TEXT,
                purchase_price REAL NOT NULL DEFAULT 0,
                selling_price REAL NOT NULL DEFAULT 0,
                quantity INTEGER NOT NULL DEFAULT 1,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS inventory_card_id_idx ON inventory(scryfall_id);
            CREATE TABLE IF NOT EXISTS watchlist (
                entry_id INTEGER PRIMARY KEY,
                scryfall_id TEXT NOT NULL DEFAULT '',
                foil INTEGER NOT NULL DEFAULT 0,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS price_snapshots (
                snapshot_id INTEGER PRIMARY KEY,
                scryfall_id TEXT NOT NULL,
                foil INTEGER NOT NULL,
                language TEXT NOT NULL,
                condition TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                price_eur REAL NOT NULL,
                UNIQUE(scryfall_id, foil, language, condition, snapshot_date)
            );
            CREATE INDEX IF NOT EXISTS price_snapshots_lookup_idx
                ON price_snapshots(scryfall_id, snapshot_date);
            CREATE TABLE IF NOT EXISTS source_health (
                source TEXT PRIMARY KEY,
                checked_at TEXT NOT NULL,
                succeeded INTEGER NOT NULL,
                details TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS alerts (
                alert_id TEXT PRIMARY KEY,
                signal_type TEXT NOT NULL,
                scryfall_id TEXT,
                created_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alert_evaluations (
                alert_id TEXT NOT NULL REFERENCES alerts(alert_id) ON DELETE CASCADE,
                horizon_days INTEGER NOT NULL,
                target_date TEXT NOT NULL,
                observed_date TEXT NOT NULL,
                reference_price REAL NOT NULL,
                observed_price REAL NOT NULL,
                change_pct REAL NOT NULL,
                direction_correct INTEGER NOT NULL,
                evaluated_at TEXT NOT NULL,
                PRIMARY KEY(alert_id, horizon_days)
            );
            CREATE TABLE IF NOT EXISTS bulk_prints (
                scryfall_id TEXT PRIMARY KEY,
                oracle_id TEXT,
                name TEXT NOT NULL,
                set_code TEXT NOT NULL,
                released_at TEXT,
                legalities TEXT NOT NULL,
                reserved INTEGER NOT NULL DEFAULT 0,
                price_eur REAL,
                price_eur_foil REAL,
                edhrec_rank INTEGER
            );
            CREATE TABLE IF NOT EXISTS card_demand_snapshots (
                scryfall_id TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                edhrec_rank INTEGER NOT NULL,
                PRIMARY KEY(scryfall_id, snapshot_date)
            );
            CREATE INDEX IF NOT EXISTS card_demand_history_idx
                ON card_demand_snapshots(scryfall_id, snapshot_date);
            """
            if database_url:
                schema = schema.replace("INTEGER PRIMARY KEY", "BIGSERIAL PRIMARY KEY")
            connection.executescript(schema)
            if database_url:
                bulk_columns = {
                    row["name"] for row in connection.execute(
                        """SELECT column_name AS name FROM information_schema.columns
                           WHERE table_schema = current_schema() AND table_name = 'bulk_prints'"""
                    ).fetchall()
                }
            else:
                bulk_columns = {
                    row["name"] for row in connection.execute("PRAGMA table_info(bulk_prints)")
                }
            column_types = {
                "price_eur": "REAL",
                "price_eur_foil": "REAL",
                "edhrec_rank": "INTEGER",
            }
            for column_name, column_type in column_types.items():
                if column_name not in bulk_columns:
                    connection.execute(
                        f"ALTER TABLE bulk_prints ADD COLUMN {column_name} {column_type}"
                    )
            _migrate_legacy_data(connection)
        _INITIALIZED_PATHS.add(database_path)


@contextmanager
def _database():
    initialize_database()
    connection = _connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _replace_inventory(connection, collection):
    connection.execute("DELETE FROM inventory")
    connection.executemany(
        """INSERT INTO inventory(
            scryfall_id, foil, language, condition, set_code, collector_number,
            status, date_added, date_sold, purchase_price, selling_price, quantity, payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                str(card.get("scryfall_id", "")),
                int(bool(card.get("foil"))),
                str(card.get("language", "FR")),
                str(card.get("condition", "NM")),
                str(card.get("set_code", "")),
                str(card.get("collector_number", "")),
                str(card.get("status", "FOR_SALE")),
                card.get("date_added"),
                card.get("date_sold"),
                float(card.get("purchase_price") or 0),
                float(card.get("selling_price") or 0),
                int(card.get("quantity") or 1),
                _serialize(card),
            )
            for card in collection
        ],
    )


def _replace_watchlist(connection, watchlist):
    connection.execute("DELETE FROM watchlist")
    connection.executemany(
        "INSERT INTO watchlist(scryfall_id, foil, payload) VALUES (?, ?, ?)",
        [
            (
                str(card.get("scryfall_id", "")),
                int(bool(card.get("foil"))),
                _serialize(card),
            )
            for card in watchlist
        ],
    )


def get_collection():
    with _database() as connection:
        rows = connection.execute("SELECT payload FROM inventory ORDER BY entry_id").fetchall()
    return [json.loads(row["payload"]) for row in rows]


def save_collection(collection):
    with _database() as connection:
        _replace_inventory(connection, collection)


def get_watchlist():
    with _database() as connection:
        rows = connection.execute("SELECT payload FROM watchlist ORDER BY entry_id").fetchall()
    return [json.loads(row["payload"]) for row in rows]


def save_watchlist(watchlist):
    with _database() as connection:
        _replace_watchlist(connection, watchlist)


def _insert_snapshot(connection, scryfall_id, foil, language, condition, snapshot_day, observed_at, price):
    connection.execute(
        """INSERT INTO price_snapshots(
            scryfall_id, foil, language, condition, snapshot_date, observed_at, price_eur
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scryfall_id, foil, language, condition, snapshot_date)
        DO UPDATE SET observed_at = excluded.observed_at, price_eur = excluded.price_eur""",
        (
            str(scryfall_id), int(bool(foil)), str(language), str(condition),
            snapshot_day, observed_at, float(price),
        ),
    )


def record_price_snapshot(scryfall_id, price, foil=False, language="FR", condition="NM", observed_at=None):
    if price in (None, ""):
        return False
    observed_at = observed_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    snapshot_day = observed_at[:10]
    date.fromisoformat(snapshot_day)
    with _database() as connection:
        _insert_snapshot(
            connection, scryfall_id, foil, language, condition,
            snapshot_day, observed_at, float(price),
        )
    return True


def get_price_history():
    with _database() as connection:
        rows = connection.execute(
            """SELECT scryfall_id, foil, snapshot_date, price_eur
               FROM price_snapshots ORDER BY snapshot_date, snapshot_id"""
        ).fetchall()
    history = {}
    for row in rows:
        point = history.setdefault(row["scryfall_id"], {}).setdefault(row["snapshot_date"], {})
        point["eur_foil" if row["foil"] else "eur"] = row["price_eur"]
    return history


def get_variant_price_history(scryfall_id, foil=False, language="FR", condition="NM"):
    with _database() as connection:
        rows = connection.execute(
            """SELECT snapshot_date, observed_at, price_eur FROM price_snapshots
               WHERE scryfall_id = ? AND foil = ? AND language = ? AND condition = ?
               ORDER BY snapshot_date""",
            (str(scryfall_id), int(bool(foil)), str(language), str(condition)),
        ).fetchall()
    return [
        {"observed_at": row["observed_at"], "snapshot_date": row["snapshot_date"], "price_eur": row["price_eur"]}
        for row in rows
    ]


def record_scryfall_prices(card, variants):
    prices = card.get("prices") or {}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records = []
    for variant in variants:
        foil = bool(variant.get("foil"))
        price = prices.get("eur_foil" if foil else "eur")
        if price is None:
            continue
        records.append((
            card.get("id"), float(price), foil,
            variant.get("language", "FR"), variant.get("condition", "NM"),
        ))
    if not records:
        return 0
    with _database() as connection:
        for scryfall_id, price, foil, language, condition in records:
            _insert_snapshot(
                connection, scryfall_id, foil, language, condition,
                now[:10], now, price,
            )
    return len(records)


def record_source_health(source, succeeded, details="", checked_at=None):
    checked_at = checked_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _database() as connection:
        connection.execute(
            """INSERT INTO source_health(source, checked_at, succeeded, details)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(source) DO UPDATE SET checked_at = excluded.checked_at,
                   succeeded = excluded.succeeded, details = excluded.details""",
            (source, checked_at, int(bool(succeeded)), str(details)),
        )


def get_source_health():
    with _database() as connection:
        rows = connection.execute(
            "SELECT source, checked_at, succeeded, details FROM source_health"
        ).fetchall()
    return {
        row["source"]: {
            "checked_at": row["checked_at"],
            "succeeded": bool(row["succeeded"]),
            "details": row["details"],
        }
        for row in rows
    }


def get_metadata(key):
    with _database() as connection:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else None


def set_metadata(key, value):
    with _database() as connection:
        connection.execute(
            """INSERT INTO metadata(key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, str(value)),
        )


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
        for card in cards:
            scryfall_id = card.get("id")
            if not scryfall_id:
                continue
            oracle_id = card.get("oracle_id")
            legalities = card.get("legalities") or {}
            prices = card.get("prices") or {}
            edhrec_rank = _as_optional_int(card.get("edhrec_rank"))
            old = connection.execute(
                "SELECT oracle_id, legalities FROM bulk_prints WHERE scryfall_id = ?",
                (scryfall_id,),
            ).fetchone()
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
                prior_print = connection.execute(
                    "SELECT 1 FROM bulk_prints WHERE oracle_id = ? LIMIT 1", (oracle_id,)
                ).fetchone() if oracle_id else None
                event = {
                    "scryfall_id": scryfall_id,
                    "oracle_id": oracle_id,
                    "name": card.get("name", "Carte inconnue"),
                    "set_code": card.get("set", ""),
                    "released_at": card.get("released_at"),
                }
                if prior_print:
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

            connection.execute(
                """INSERT INTO bulk_prints(
                       scryfall_id, oracle_id, name, set_code, released_at, legalities, reserved
                       , price_eur, price_eur_foil, edhrec_rank) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(scryfall_id) DO UPDATE SET oracle_id = excluded.oracle_id,
                       name = excluded.name, set_code = excluded.set_code,
                       released_at = excluded.released_at, legalities = excluded.legalities,
                           reserved = excluded.reserved, price_eur = excluded.price_eur,
                           price_eur_foil = excluded.price_eur_foil,
                           edhrec_rank = excluded.edhrec_rank""",
                (
                    scryfall_id, oracle_id, card.get("name", "Carte inconnue"),
                    card.get("set", ""), card.get("released_at"),
                        _serialize(legalities), int(bool(card.get("reserved"))),
                        _as_optional_price(prices.get("eur")),
                        _as_optional_price(prices.get("eur_foil")),
                        edhrec_rank,
                ),
            )
            if edhrec_rank is not None:
                connection.execute(
                    """INSERT INTO card_demand_snapshots(scryfall_id, snapshot_date, edhrec_rank)
                       VALUES (?, ?, ?)
                       ON CONFLICT(scryfall_id, snapshot_date)
                       DO UPDATE SET edhrec_rank = excluded.edhrec_rank""",
                    (scryfall_id, snapshot_day, edhrec_rank),
                )
            connection.execute(
                "INSERT INTO seen_bulk_prints(scryfall_id) VALUES (?)", (scryfall_id,)
            )
        if not summary["baseline"]:
            connection.execute(
                "DELETE FROM bulk_prints WHERE scryfall_id NOT IN (SELECT scryfall_id FROM seen_bulk_prints)"
            )
    return summary


def _store_alert(connection, alert_id, signal_type, scryfall_id, created_at, payload):
    connection.execute(
        """INSERT INTO alerts(alert_id, signal_type, scryfall_id, created_at, payload)
           VALUES (?, ?, ?, ?, ?) ON CONFLICT(alert_id) DO NOTHING""",
        (alert_id, signal_type, scryfall_id, created_at, _serialize(payload)),
    )


def record_alert(alert_id, signal_type, scryfall_id, created_at, payload):
    with _database() as connection:
        _store_alert(connection, alert_id, signal_type, scryfall_id, created_at, payload)


def get_price_points(scryfall_id, foil=False, language="FR", condition="NM"):
    with _database() as connection:
        rows = connection.execute(
            """SELECT snapshot_date, price_eur FROM price_snapshots
               WHERE scryfall_id = ? AND foil = ? AND language = ? AND condition = ?
               ORDER BY snapshot_date""",
            (str(scryfall_id), int(bool(foil)), str(language), str(condition)),
        ).fetchall()
    return [(row["snapshot_date"], row["price_eur"]) for row in rows]


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


def get_alerts_for_evaluation():
    with _database() as connection:
        rows = connection.execute(
            """SELECT a.alert_id, a.signal_type, a.scryfall_id, a.created_at, a.payload
               FROM alerts AS a
               WHERE a.signal_type IN ('BUY_SIGNAL', 'SELL_RECOMMENDATION', 'PRICE_DROP')
                 AND (SELECT COUNT(*) FROM alert_evaluations e WHERE e.alert_id = a.alert_id) < 2
               ORDER BY a.created_at"""
        ).fetchall()
    return [
        {
            "alert_id": row["alert_id"], "signal_type": row["signal_type"],
            "scryfall_id": row["scryfall_id"], "created_at": row["created_at"],
            "payload": json.loads(row["payload"]),
        }
        for row in rows
    ]


def record_alert_evaluation(alert_id, horizon_days, target_date, observed_date,
                            reference_price, observed_price, change_pct,
                            direction_correct, evaluated_at=None):
    evaluated_at = evaluated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _database() as connection:
        connection.execute(
                """INSERT INTO alert_evaluations(
                   alert_id, horizon_days, target_date, observed_date, reference_price,
                   observed_price, change_pct, direction_correct, evaluated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(alert_id, horizon_days) DO NOTHING""",
            (
                alert_id, int(horizon_days), target_date, observed_date,
                float(reference_price), float(observed_price), float(change_pct),
                int(bool(direction_correct)), evaluated_at,
            ),
        )


def get_alert_metrics():
    with _database() as connection:
        rows = connection.execute(
            """SELECT a.signal_type, e.horizon_days, COUNT(*) AS sample_count,
                      AVG(e.direction_correct) AS accuracy,
                      AVG(e.change_pct) AS average_change_pct
               FROM alert_evaluations e JOIN alerts a ON a.alert_id = e.alert_id
               GROUP BY a.signal_type, e.horizon_days
               ORDER BY a.signal_type, e.horizon_days"""
        ).fetchall()
    return [dict(row) for row in rows]


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


BACKUP_TABLE_COLUMNS = {
    "inventory": (
        "scryfall_id", "foil", "language", "condition", "set_code",
        "collector_number", "status", "date_added", "date_sold",
        "purchase_price", "selling_price", "quantity", "payload",
    ),
    "watchlist": ("scryfall_id", "foil", "payload"),
    "price_snapshots": (
        "scryfall_id", "foil", "language", "condition", "snapshot_date",
        "observed_at", "price_eur",
    ),
    "source_health": ("source", "checked_at", "succeeded", "details"),
    "bulk_prints": (
        "scryfall_id", "oracle_id", "name", "set_code", "released_at",
        "legalities", "reserved", "price_eur", "price_eur_foil", "edhrec_rank",
    ),
    "card_demand_snapshots": ("scryfall_id", "snapshot_date", "edhrec_rank"),
    "alerts": ("alert_id", "signal_type", "scryfall_id", "created_at", "payload"),
    "alert_evaluations": (
        "alert_id", "horizon_days", "target_date", "observed_date",
        "reference_price", "observed_price", "change_pct", "direction_correct",
        "evaluated_at",
    ),
    "metadata": ("key", "value"),
}
BACKUP_FORMAT_VERSION = 1


def export_database_snapshot():
    with _database() as connection:
        tables = {}
        for table_name, columns in BACKUP_TABLE_COLUMNS.items():
            order_by = {
                "inventory": "entry_id",
                "watchlist": "entry_id",
                "price_snapshots": "snapshot_date, snapshot_id",
            }.get(table_name)
            ordering = f" ORDER BY {order_by}" if order_by else ""
            tables[table_name] = [
                dict(row) for row in connection.execute(
                    f"SELECT {', '.join(columns)} FROM {table_name}{ordering}"
                ).fetchall()
            ]
    return {
        "format": "magic-trading-sql-backup",
        "format_version": BACKUP_FORMAT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tables": tables,
    }


def restore_database_snapshot(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("format") != "magic-trading-sql-backup":
        raise ValueError("Format de sauvegarde inconnu.")
    if snapshot.get("format_version") != BACKUP_FORMAT_VERSION:
        raise ValueError("Version de sauvegarde non prise en charge.")
    tables = snapshot.get("tables")
    if not isinstance(tables, dict) or set(tables) != set(BACKUP_TABLE_COLUMNS):
        raise ValueError("La sauvegarde est incomplète ou contient un schéma inattendu.")
    for table_name, columns in BACKUP_TABLE_COLUMNS.items():
        rows = tables[table_name]
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not set(columns).issubset(row)
            for row in rows
        ):
            raise ValueError(f"Données invalides dans la table {table_name}.")

    delete_order = (
        "alert_evaluations", "alerts", "bulk_prints", "source_health",
        "card_demand_snapshots", "price_snapshots", "watchlist", "inventory", "metadata",
    )
    with _database() as connection:
        for table_name in delete_order:
            connection.execute(f"DELETE FROM {table_name}")
        for table_name, columns in BACKUP_TABLE_COLUMNS.items():
            rows = tables[table_name]
            if not rows:
                continue
            placeholders = ", ".join("?" for _ in columns)
            column_sql = ", ".join(columns)
            connection.executemany(
                f"INSERT INTO {table_name} ({column_sql}) VALUES ({placeholders})",
                [tuple(row[column] for column in columns) for row in rows],
            )
    return {table_name: len(rows) for table_name, rows in tables.items()}
