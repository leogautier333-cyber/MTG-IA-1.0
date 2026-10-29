import hashlib
import json
import os
import sqlite3
import threading
from contextlib import closing, contextmanager
from datetime import date, datetime, timezone
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
    "price_alerts",
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
    price_alerts_migrated = connection.execute(
        "SELECT value FROM metadata WHERE key = 'legacy_price_alerts_migrated'"
    ).fetchone()
    if not price_alerts_migrated:
        legacy_alerts = _read_json(legacy_dir / "price_alerts.json", [])
        for alert in legacy_alerts if isinstance(legacy_alerts, list) else []:
            if isinstance(alert, dict) and alert.get("id"):
                _upsert_price_alert(connection, alert)
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES ('legacy_price_alerts_migrated', '1')"
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
            CREATE TABLE IF NOT EXISTS price_alerts (
                alert_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT '',
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


def _inventory_row(card):
    return (
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


_INSERT_INVENTORY = """INSERT INTO inventory(
    scryfall_id, foil, language, condition, set_code, collector_number,
    status, date_added, date_sold, purchase_price, selling_price, quantity, payload
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""


def _replace_inventory(connection, collection):
    connection.execute("DELETE FROM inventory")
    connection.executemany(_INSERT_INVENTORY, [_inventory_row(card) for card in collection])


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


def append_inventory_card(card):
    with _database() as connection:
        connection.execute(_INSERT_INVENTORY, _inventory_row(card))


_EDITABLE_FIELDS = ("quantity", "purchase_price", "selling_price", "target_price")


def update_inventory_card(original, quantity, purchase_price, selling_price, target_price=None):
    """Modifie uniquement la ligne identique à `original` ; False si elle a changé entre-temps.

    `target_price` : objectif de vente (0 ou None = aucun objectif)."""
    identity = ("scryfall_id", "foil", "language", "condition", "status", "date_added",
                *_EDITABLE_FIELDS)
    with _database() as connection:
        rows = connection.execute(
            "SELECT entry_id, payload FROM inventory WHERE scryfall_id = ? ORDER BY entry_id",
            (str(original.get("scryfall_id", "")),),
        ).fetchall()
        for row in rows:
            card = json.loads(row["payload"])
            if any(card.get(key) != original.get(key) for key in identity):
                continue
            card.update(
                quantity=int(quantity), purchase_price=float(purchase_price),
                selling_price=float(selling_price),
            )
            if target_price:
                card["target_price"] = float(target_price)
            else:
                card.pop("target_price", None)
            connection.execute(
                """UPDATE inventory SET quantity = ?, purchase_price = ?, selling_price = ?,
                   payload = ? WHERE entry_id = ?""",
                (card["quantity"], card["purchase_price"], card["selling_price"],
                 _serialize(card), row["entry_id"]),
            )
            return True
    return False


def mark_inventory_sold(scryfall_id, sale_price, sold_at):
    """Marque la plus ancienne copie en vente comme vendue ; retourne False si aucune."""
    with _database() as connection:
        row = connection.execute(
            """SELECT entry_id, payload FROM inventory
               WHERE scryfall_id = ? AND status = 'FOR_SALE'
               ORDER BY entry_id LIMIT 1""",
            (str(scryfall_id),),
        ).fetchone()
        if not row:
            return False
        card = json.loads(row["payload"])
        card.update(status="SOLD", actual_sale_price=float(sale_price), date_sold=sold_at)
        connection.execute(
            "UPDATE inventory SET status = 'SOLD', date_sold = ?, payload = ? WHERE entry_id = ?",
            (sold_at, _serialize(card), row["entry_id"]),
        )
    return True


def get_price_alerts():
    with _database() as connection:
        rows = connection.execute(
            "SELECT payload FROM price_alerts ORDER BY created_at, alert_id"
        ).fetchall()
    return [json.loads(row["payload"]) for row in rows]


def _upsert_price_alert(connection, alert):
    connection.execute(
        """INSERT INTO price_alerts(alert_id, status, created_at, payload)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(alert_id) DO UPDATE SET status = excluded.status, payload = excluded.payload""",
        (
            str(alert["id"]), str(alert.get("status", "pending")),
            str(alert.get("created_at", "")), _serialize(alert),
        ),
    )


def save_price_alerts(alerts):
    """Insère ou met à jour les alertes de variation de prix (clé : alert['id'])."""
    with _database() as connection:
        for alert in alerts:
            _upsert_price_alert(connection, alert)


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
