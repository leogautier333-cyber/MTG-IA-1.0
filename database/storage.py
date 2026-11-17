import hashlib
import json
import math
import os
import sqlite3
import threading
from contextlib import closing, contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit


PROJECT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DATABASE_PATH = Path(os.getenv("MTG_DATABASE_PATH", PROJECT_DATA_DIR / "mtg_manager.sqlite3"))
DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("MTG_DATABASE_URL")
_INITIALIZED_PATHS = set()
_INITIALIZATION_LOCK = threading.Lock()
SCHEMA_VERSION = 5

REQUIRED_TABLES = frozenset({
    "metadata", "inventory", "watchlist", "price_snapshots", "source_health",
    "alerts", "alert_evaluations", "bulk_prints", "card_demand_snapshots",
    "price_alerts", "inventory_events", "inventory_history_control",
})


class DatabaseConfigurationError(RuntimeError):
    """Raised when a requested database backend is missing or misconfigured."""


class DatabaseConnectionError(RuntimeError):
    """Raised when a configured database cannot be reached or initialized."""


def _is_github_actions():
    return os.getenv("GITHUB_ACTIONS", "").lower() == "true"


def _requires_postgres():
    return _is_github_actions() or os.getenv("MTG_REQUIRE_POSTGRES", "").lower() == "true"


def _missing_database_message():
    if _is_github_actions():
        return "DATABASE_URL is required in GitHub Actions; refusing to use ephemeral SQLite."
    return "DATABASE_URL is required when MTG_REQUIRE_POSTGRES=true; refusing to use SQLite."


def _configured_database_url():
    return os.getenv("DATABASE_URL") or os.getenv("MTG_DATABASE_URL") or DATABASE_URL


def get_database_backend():
    if _configured_database_url():
        return "postgres"
    if _requires_postgres():
        raise DatabaseConfigurationError(_missing_database_message())
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

    if _requires_postgres():
        raise DatabaseConfigurationError(_missing_database_message())

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
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError) as error:
        raise DatabaseConfigurationError(
            f"Cannot read legacy data file {path.name}; migration stopped."
        ) from error


def _serialize(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _migrate_legacy_data(connection):
    migrated = connection.execute(
        "SELECT value FROM metadata WHERE key = 'legacy_json_migrated'"
    ).fetchone()
    legacy_dir = DATABASE_PATH.parent
    if not migrated:
        collection = _read_json(legacy_dir / "collection.json", None)
        watchlist = _read_json(legacy_dir / "watchlist.json", None)
        for table, records, replace in (
            ("inventory", collection, _replace_inventory),
            ("watchlist", watchlist, _replace_watchlist),
        ):
            if records is None:
                continue
            if not isinstance(records, list):
                raise DatabaseConfigurationError(f"Invalid legacy data for {table}; migration stopped.")
            if connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]:
                raise DatabaseConfigurationError(
                    f"Cannot migrate legacy {table} into a non-empty table; manual review required."
                )
            replace(connection, records)
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
                    snapshot_day, observed_at, price, preserve_existing=True,
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
                        snapshot_day, str(timestamp), float(price), preserve_existing=True,
                    )
                except (TypeError, ValueError):
                    continue


def _migrate_schema(connection):
    row = connection.execute(
        "SELECT value FROM metadata WHERE key = 'schema_version'"
    ).fetchone()
    try:
        version = int(row["value"]) if row else 0
    except (TypeError, ValueError) as error:
        raise DatabaseConfigurationError("Invalid database schema version.") from error
    if version < 0 or version > SCHEMA_VERSION:
        raise DatabaseConfigurationError(
            f"Unsupported database schema version {version}; update the application."
        )
    if version < 1:
        if connection.dialect == "postgres":
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
        for column_name, column_type in (
            ("price_eur", "REAL"), ("price_eur_foil", "REAL"), ("edhrec_rank", "INTEGER")
        ):
            if column_name not in bulk_columns:
                connection.execute(f"ALTER TABLE bulk_prints ADD COLUMN {column_name} {column_type}")
        connection.execute(
            """INSERT INTO metadata(key, value) VALUES ('schema_version', '1')
               ON CONFLICT(key) DO UPDATE SET value = excluded.value"""
        )
    if version < 2:
        _create_inventory_history(connection)
        connection.execute(
            """INSERT INTO metadata(key, value) VALUES ('schema_version', '2')
               ON CONFLICT(key) DO UPDATE SET value = excluded.value"""
        )
    if version < 3:
        _create_inventory_validation(connection)
        connection.execute(
            """INSERT INTO metadata(key, value) VALUES ('schema_version', '3')
               ON CONFLICT(key) DO UPDATE SET value = excluded.value"""
        )
    if version < 4:
        _create_price_snapshot_validation(connection)
        connection.execute(
            """INSERT INTO metadata(key, value) VALUES ('schema_version', '4')
               ON CONFLICT(key) DO UPDATE SET value = excluded.value"""
        )
    if version < 5:
        _widen_postgres_money_columns(connection)
        connection.execute(
            """INSERT INTO metadata(key, value) VALUES ('schema_version', '5')
               ON CONFLICT(key) DO UPDATE SET value = excluded.value"""
        )


_MONEY_COLUMNS = (
    ("inventory", "purchase_price"), ("inventory", "selling_price"),
    ("price_snapshots", "price_eur"),
    ("bulk_prints", "price_eur"), ("bulk_prints", "price_eur_foil"),
    ("alert_evaluations", "reference_price"), ("alert_evaluations", "observed_price"),
    ("alert_evaluations", "change_pct"),
)


def _widen_postgres_money_columns(connection):
    """PostgreSQL REAL (4 octets, ~7 chiffres) arrondit les gros montants ; SQLite REAL a 8 octets.

    La conversion passe par le texte le plus court du REAL (PostgreSQL ≥ 12) pour conserver
    la valeur lue aujourd'hui (19.99 et non 19.989999771118164)."""
    if connection.dialect != "postgres":
        return
    for table, column in _MONEY_COLUMNS:
        connection.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE DOUBLE PRECISION "
            f"USING {column}::text::double precision"
        )


def _create_price_snapshot_validation(connection):
    if connection.dialect == "postgres":
        connection.execute("""
            CREATE FUNCTION validate_price_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.price_eur < 0 OR NEW.price_eur >= 'Infinity'::real THEN
                    RAISE EXCEPTION 'Invalid snapshot price';
                END IF;
                RETURN NEW;
            END;
            $$
        """)
        connection.execute("""CREATE TRIGGER price_snapshot_validation_trigger
            BEFORE INSERT OR UPDATE ON price_snapshots
            FOR EACH ROW EXECUTE FUNCTION validate_price_snapshot()""")
    else:
        for action in ("INSERT", "UPDATE"):
            connection.execute(f"""CREATE TRIGGER price_snapshot_validation_{action.lower()}
                BEFORE {action} ON price_snapshots
                WHEN typeof(NEW.price_eur) NOT IN ('integer', 'real')
                  OR NEW.price_eur < 0 OR NEW.price_eur > 3.4028234663852886e38
                BEGIN
                    SELECT RAISE(ABORT, 'Invalid snapshot price');
                END""")


def _create_inventory_validation(connection):
    if connection.dialect == "postgres":
        connection.execute("""
            CREATE FUNCTION validate_inventory_values() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.quantity < 1 OR NEW.purchase_price < 0 OR NEW.selling_price < 0
                   OR NEW.purchase_price >= 'Infinity'::real
                   OR NEW.selling_price >= 'Infinity'::real THEN
                    RAISE EXCEPTION 'Invalid inventory quantity or price';
                END IF;
                RETURN NEW;
            END;
            $$
        """)
        connection.execute("""CREATE TRIGGER inventory_validation_trigger
            BEFORE INSERT OR UPDATE ON inventory
            FOR EACH ROW EXECUTE FUNCTION validate_inventory_values()""")
    else:
        for action in ("INSERT", "UPDATE"):
            connection.execute(f"""CREATE TRIGGER inventory_validation_{action.lower()}
                BEFORE {action} ON inventory
                WHEN typeof(NEW.quantity) != 'integer' OR NEW.quantity < 1
                  OR NEW.quantity > 2147483647
                  OR typeof(NEW.purchase_price) NOT IN ('integer', 'real')
                  OR typeof(NEW.selling_price) NOT IN ('integer', 'real')
                  OR NEW.purchase_price < 0 OR NEW.selling_price < 0
                  OR NEW.purchase_price > 3.4028234663852886e38
                  OR NEW.selling_price > 3.4028234663852886e38
                BEGIN
                    SELECT RAISE(ABORT, 'Invalid inventory quantity or price');
                END""")


def _create_inventory_history(connection):
    if connection.dialect == "postgres":
        connection.execute("LOCK TABLE inventory IN SHARE ROW EXCLUSIVE MODE")
        event_id = "BIGSERIAL PRIMARY KEY"
        timestamp = "to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')"
    else:
        event_id = "INTEGER PRIMARY KEY"
        timestamp = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
    connection.execute(f"""CREATE TABLE inventory_events (
        event_id {event_id}, occurred_at TEXT NOT NULL, old_payload TEXT, new_payload TEXT
    )""")
    connection.execute("""CREATE TABLE inventory_history_control (
        id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL
    )""")
    connection.execute("INSERT INTO inventory_history_control(id, enabled) VALUES (1, 0)")
    _reset_inventory_history(connection)
    if connection.dialect == "postgres":
        connection.execute(f"""
            CREATE FUNCTION record_inventory_event() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF (SELECT enabled FROM inventory_history_control WHERE id = 1) = 1 THEN
                    IF TG_OP = 'INSERT' THEN
                        INSERT INTO inventory_events(occurred_at, old_payload, new_payload)
                        VALUES ({timestamp}, NULL, NEW.payload);
                    ELSIF TG_OP = 'DELETE' THEN
                        INSERT INTO inventory_events(occurred_at, old_payload, new_payload)
                        VALUES ({timestamp}, OLD.payload, NULL);
                    ELSIF OLD.payload IS DISTINCT FROM NEW.payload THEN
                        INSERT INTO inventory_events(occurred_at, old_payload, new_payload)
                        VALUES ({timestamp}, OLD.payload, NEW.payload);
                    END IF;
                END IF;
                RETURN NULL;
            END;
            $$
        """)
        connection.execute("""CREATE TRIGGER inventory_history_trigger
            AFTER INSERT OR UPDATE OR DELETE ON inventory
            FOR EACH ROW EXECUTE FUNCTION record_inventory_event()""")
    else:
        for action, old, new in (
            ("INSERT", "NULL", "NEW.payload"),
            ("UPDATE", "OLD.payload", "NEW.payload"),
            ("DELETE", "OLD.payload", "NULL"),
        ):
            condition = " AND OLD.payload <> NEW.payload" if action == "UPDATE" else ""
            connection.execute(f"""CREATE TRIGGER inventory_history_{action.lower()}
                AFTER {action} ON inventory
                WHEN (SELECT enabled FROM inventory_history_control WHERE id = 1) = 1{condition}
                BEGIN
                    INSERT INTO inventory_events(occurred_at, old_payload, new_payload)
                    VALUES ({timestamp}, {old}, {new});
                END""")
    connection.execute("UPDATE inventory_history_control SET enabled = 1 WHERE id = 1")


def _reset_inventory_history(connection):
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    connection.execute("DELETE FROM inventory_events")
    connection.execute(
        "INSERT INTO inventory_events(occurred_at, old_payload, new_payload) VALUES (?, NULL, NULL)",
        (now,),
    )
    connection.execute(
        """INSERT INTO inventory_events(occurred_at, old_payload, new_payload)
           SELECT ?, NULL, payload FROM inventory ORDER BY entry_id""",
        (now,),
    )


def get_inventory_events():
    with _database() as connection:
        rows = connection.execute(
            "SELECT occurred_at, old_payload, new_payload FROM inventory_events ORDER BY event_id"
        ).fetchall()
    return [
        {
            "occurred_at": row["occurred_at"],
            "old": json.loads(row["old_payload"]) if row["old_payload"] is not None else None,
            "new": json.loads(row["new_payload"]) if row["new_payload"] is not None else None,
        }
        for row in rows
    ]


def initialize_database():
    database_url = _configured_database_url()
    if not database_url and _requires_postgres():
        raise DatabaseConfigurationError(_missing_database_message())
    database_path = database_url or str(DATABASE_PATH.resolve())
    if database_path in _INITIALIZED_PATHS:
        return
    with _INITIALIZATION_LOCK:
        if database_path in _INITIALIZED_PATHS:
            return
        with closing(_connect()) as connection:
            if database_url:
                connection.execute("SELECT pg_advisory_xact_lock(?)", (734526418,))
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
            if not database_url:
                connection.execute("BEGIN IMMEDIATE")
            _migrate_schema(connection)
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


def validate_inventory_quantity(value):
    try:
        quantity = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("La quantité doit être un entier positif.") from None
    if not quantity.is_finite() or quantity != quantity.to_integral_value() or not 1 <= quantity <= 2147483647:
        raise ValueError("La quantité doit être un entier positif compatible avec la base.")
    return int(quantity)


def validate_monetary_amount(value, field):
    try:
        amount = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{field} doit être un montant positif ou nul.") from None
    if not math.isfinite(amount) or amount < 0:
        raise ValueError(f"{field} doit être un montant fini, positif ou nul.")
    return amount


def _inventory_row(card):
    for field in ("target_price", "actual_sale_price"):
        if card.get(field) is not None:
            validate_monetary_amount(card[field], field)
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
        validate_monetary_amount(card.get("purchase_price") or 0, "Le prix d'achat"),
        validate_monetary_amount(card.get("selling_price") or 0, "Le prix de vente"),
        validate_inventory_quantity(card.get("quantity") if card.get("quantity") is not None else 1),
        _serialize(card),
    )


_INSERT_INVENTORY = """INSERT INTO inventory(
    scryfall_id, foil, language, condition, set_code, collector_number,
    status, date_added, date_sold, purchase_price, selling_price, quantity, payload
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""


def _replace_inventory(connection, collection):
    connection.execute("DELETE FROM inventory")
    connection.executemany(_INSERT_INVENTORY, [_inventory_row(card) for card in collection])


def find_invalid_price_snapshots(connection=None):
    """Retourne les snapshot_id de prix négatifs ou hors plage, déjà stockés (lecture seule)."""
    query = """SELECT snapshot_id FROM price_snapshots
               WHERE price_eur < 0 OR price_eur > 3.4028234663852886e38
               ORDER BY snapshot_id"""
    if connection is not None:
        return [row["snapshot_id"] for row in connection.execute(query).fetchall()]
    with _database() as active:
        return [row["snapshot_id"] for row in active.execute(query).fetchall()]


def find_inventory_inconsistencies(connection=None):
    """Retourne les entry_id dont les colonnes SQL divergent du payload JSON (lecture seule)."""
    columns = ("scryfall_id", "foil", "language", "condition", "set_code", "collector_number",
               "status", "date_added", "date_sold", "purchase_price", "selling_price", "quantity")

    def same(stored, expected):
        if isinstance(expected, float) or isinstance(stored, float):
            try:
                return math.isclose(float(stored), float(expected), rel_tol=1e-6, abs_tol=1e-9)
            except (TypeError, ValueError):
                return False
        return stored == expected

    def inspect(active):
        issues = []
        rows = active.execute(
            f"SELECT entry_id, {', '.join(columns)}, payload FROM inventory ORDER BY entry_id"
        ).fetchall()
        for row in rows:
            try:
                expected = _inventory_row(json.loads(row["payload"]))[:-1]
            except (ValueError, TypeError, json.JSONDecodeError):
                issues.append(row["entry_id"])
                continue
            if not all(same(row[name], value) for name, value in zip(columns, expected)):
                issues.append(row["entry_id"])
        return issues

    if connection is not None:
        return inspect(connection)
    with _database() as active:
        return inspect(active)


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


def append_inventory_cards(cards):
    """Ajoute plusieurs cartes en une seule transaction (tout ou rien)."""
    with _database() as connection:
        connection.executemany(_INSERT_INVENTORY, [_inventory_row(card) for card in cards])


def synchronize_cardmarket_stock(cards, expected_collection, synced_at):
    """Synchronise les lignes en vente en une transaction, sans toucher aux ventes."""
    def variant(card):
        return (
            str(card.get("scryfall_id", "")), bool(card.get("foil")),
            str(card.get("language", "FR")).upper(), str(card.get("condition", "NM")).upper(),
        )

    def counts(items, key_function):
        result = {}
        for item in items:
            key = key_function(item)
            result[key] = result.get(key, 0) + 1
        return result

    with _database() as connection:
        if connection.dialect == "postgres":
            connection.execute("LOCK TABLE inventory IN EXCLUSIVE MODE")
        else:
            connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            "SELECT entry_id, payload FROM inventory ORDER BY entry_id"
        ).fetchall()
        existing = [(row["entry_id"], json.loads(row["payload"])) for row in rows]
        if [card for _, card in existing] != expected_collection:
            raise ValueError("Le stock a changé depuis l'aperçu ; recommence la préparation.")
        active = [(entry_id, card) for entry_id, card in existing
                  if card.get("status", "FOR_SALE") == "FOR_SALE"]
        article_counts = counts(
            [card for _, card in active if card.get("cardmarket_article_id")],
            lambda card: str(card["cardmarket_article_id"]),
        )
        incoming_articles = counts(cards, lambda card: str(card["cardmarket_article_id"]))
        if any(count != 1 for count in article_counts.values()) or any(
            count != 1 for count in incoming_articles.values()
        ):
            raise ValueError("Identifiants Cardmarket en double ; synchronisation refusée.")
        old_variants = counts([card for _, card in active], variant)
        new_variants = counts(cards, variant)
        active_by_article = {}
        for entry_id, card in active:
            article = str(card.get("cardmarket_article_id", ""))
            if article:
                active_by_article.setdefault(article, (entry_id, card))
        manual_by_variant = {
            variant(card): (entry_id, card)
            for entry_id, card in active if not card.get("cardmarket_article_id")
        }
        used = set()
        matched = added = 0
        for incoming in cards:
            article = str(incoming["cardmarket_article_id"])
            match = active_by_article.get(article)
            if (match is None and incoming.get("scryfall_id")
                    and old_variants.get(variant(incoming)) == 1 and new_variants[variant(incoming)] == 1):
                match = manual_by_variant.get(variant(incoming))
            if match is not None and match[0] not in used:
                entry_id, current = match
                if (current.get("cardmarket_product_id")
                        and str(current["cardmarket_product_id"]) != str(incoming["cardmarket_product_id"])):
                    raise ValueError(f"Produit modifié pour l'article {article} ; synchronisation refusée.")
                updated = {**current, **incoming, "status": "FOR_SALE"}
                used.add(entry_id)
                matched += 1
                connection.execute(
                    """UPDATE inventory SET scryfall_id = ?, foil = ?, language = ?, condition = ?,
                       set_code = ?, collector_number = ?, status = ?, date_added = ?, date_sold = ?,
                       purchase_price = ?, selling_price = ?, quantity = ?, payload = ?
                       WHERE entry_id = ?""",
                    (*_inventory_row(updated), entry_id),
                )
            else:
                new_card = {
                    **incoming, "status": "FOR_SALE", "date_added": synced_at,
                    "purchase_price": 0.0, "purchase_price_unknown": True,
                    "liquidity_rating": "MEDIUM",
                }
                connection.execute(_INSERT_INVENTORY, _inventory_row(new_card))
                added += 1
        withdrawn = 0
        for entry_id, current in active:
            if entry_id in used:
                continue
            current.update(status="WITHDRAWN", date_withdrawn=synced_at)
            connection.execute(
                "UPDATE inventory SET status = ?, payload = ? WHERE entry_id = ?",
                ("WITHDRAWN", _serialize(current), entry_id),
            )
            withdrawn += 1
    return {"matched": matched, "added": added, "withdrawn": withdrawn}


_EDITABLE_FIELDS = ("quantity", "purchase_price", "selling_price", "target_price")


def update_inventory_card(original, quantity, purchase_price, selling_price, target_price=None):
    """Modifie uniquement la ligne identique à `original` ; False si elle a changé entre-temps.

    `target_price` : objectif de vente (0 ou None = aucun objectif)."""
    quantity = validate_inventory_quantity(quantity)
    purchase_price = validate_monetary_amount(purchase_price, "Le prix d'achat")
    selling_price = validate_monetary_amount(selling_price, "Le prix de vente")
    if target_price is not None:
        target_price = validate_monetary_amount(target_price, "Le prix cible")
    identity = ("scryfall_id", "cardmarket_article_id", "foil", "language", "condition", "status", "date_added",
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
            if float(purchase_price) > 0:
                card.pop("purchase_price_unknown", None)
            if target_price:
                card["target_price"] = float(target_price)
            else:
                card.pop("target_price", None)
            cursor = connection.execute(
                """UPDATE inventory SET quantity = ?, purchase_price = ?, selling_price = ?,
                   payload = ? WHERE entry_id = ? AND payload = ?""",
                (card["quantity"], card["purchase_price"], card["selling_price"],
                 _serialize(card), row["entry_id"], row["payload"]),
            )
            return cursor.rowcount == 1
    return False


def append_inventory_decision(original, decision):
    """Ajoute une entrée au journal de décision de la ligne d'inventaire correspondante."""
    identity = (
        "scryfall_id", "cardmarket_article_id", "foil", "language", "condition", "status", "date_added",
        "set_code", "collector_number", "quantity", "purchase_price", "selling_price",
    )
    with _database() as connection:
        rows = connection.execute(
            "SELECT entry_id, payload FROM inventory WHERE scryfall_id = ? ORDER BY entry_id",
            (str(original.get("scryfall_id", "")),),
        ).fetchall()
        for row in rows:
            card = json.loads(row["payload"])
            if any(card.get(key) != original.get(key) for key in identity):
                continue
            journal = card.setdefault("decision_journal", [])
            journal.append(decision)
            cursor = connection.execute(
                "UPDATE inventory SET payload = ? WHERE entry_id = ? AND payload = ?",
                (_serialize(card), row["entry_id"], row["payload"]),
            )
            return cursor.rowcount == 1
    return False


def mark_inventory_sold(scryfall_id, sale_price, sold_at):
    """Marque la plus ancienne copie en vente comme vendue ; retourne False si aucune."""
    sale_price = validate_monetary_amount(sale_price, "Le prix de vente")
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
        cursor = connection.execute(
            """UPDATE inventory SET status = 'SOLD', date_sold = ?, payload = ?
               WHERE entry_id = ? AND status = 'FOR_SALE' AND payload = ?""",
            (sold_at, _serialize(card), row["entry_id"], row["payload"]),
        )
        return cursor.rowcount == 1


def mark_inventory_entry_sold(original, sale_price, sold_at):
    """Marque la fiche sélectionnée comme vendue si elle est toujours inchangée."""
    sale_price = validate_monetary_amount(sale_price, "Le prix de vente")
    with _database() as connection:
        rows = connection.execute(
            """SELECT entry_id, payload FROM inventory
               WHERE scryfall_id = ? AND status = 'FOR_SALE' ORDER BY entry_id""",
            (str(original.get("scryfall_id", "")),),
        ).fetchall()
        for row in rows:
            card = json.loads(row["payload"])
            if card != original:
                continue
            card.update(status="SOLD", actual_sale_price=float(sale_price), date_sold=sold_at)
            cursor = connection.execute(
                """UPDATE inventory SET status = 'SOLD', date_sold = ?, payload = ?
                   WHERE entry_id = ? AND status = 'FOR_SALE' AND payload = ?""",
                (sold_at, _serialize(card), row["entry_id"], row["payload"]),
            )
            return cursor.rowcount == 1
    return False


def get_price_alerts():
    with _database() as connection:
        rows = connection.execute(
            "SELECT payload FROM price_alerts ORDER BY created_at, alert_id"
        ).fetchall()
    return [json.loads(row["payload"]) for row in rows]


_UPSERT_PRICE_ALERT = """INSERT INTO price_alerts(alert_id, status, created_at, payload)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(alert_id) DO UPDATE SET status = excluded.status, payload = excluded.payload"""


def _price_alert_values(alert):
    return (
        str(alert["id"]), str(alert.get("status", "pending")),
        str(alert.get("created_at", "")), _serialize(alert),
    )


def _upsert_price_alert(connection, alert):
    connection.execute(_UPSERT_PRICE_ALERT, _price_alert_values(alert))


def _upsert_price_alerts(connection, alerts):
    if alerts:
        connection.executemany(_UPSERT_PRICE_ALERT, [_price_alert_values(alert) for alert in alerts])


def save_price_alerts(alerts):
    """Insère ou met à jour les alertes de variation de prix (clé : alert['id'])."""
    with _database() as connection:
        _upsert_price_alerts(connection, alerts)


def get_watchlist():
    with _database() as connection:
        rows = connection.execute("SELECT payload FROM watchlist ORDER BY entry_id").fetchall()
    return [json.loads(row["payload"]) for row in rows]


def save_watchlist(watchlist):
    with _database() as connection:
        _replace_watchlist(connection, watchlist)


def _insert_snapshot(connection, scryfall_id, foil, language, condition, snapshot_day, observed_at, price,
                     preserve_existing=False):
    price = validate_monetary_amount(price, "Le prix du relevé")
    conflict_action = (
        "DO NOTHING" if preserve_existing
        else "DO UPDATE SET observed_at = excluded.observed_at, price_eur = excluded.price_eur"
    )
    connection.execute(
        f"""INSERT INTO price_snapshots(
            scryfall_id, foil, language, condition, snapshot_date, observed_at, price_eur
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scryfall_id, foil, language, condition, snapshot_date)
        {conflict_action}""",
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
               FROM price_snapshots WHERE language = 'FR' AND condition = 'NM'
               ORDER BY snapshot_date, snapshot_id"""
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


def _get_latest_stock_snapshots(connection, card_ids):
    result = {}
    for start in range(0, len(card_ids), 500):
        chunk = card_ids[start:start + 500]
        placeholders = ", ".join("?" for _ in chunk)
        rows = connection.execute(
            f"""SELECT scryfall_id, foil, observed_at, price_eur FROM (
                    SELECT scryfall_id, foil, observed_at, price_eur,
                           ROW_NUMBER() OVER (
                               PARTITION BY scryfall_id, foil ORDER BY snapshot_date DESC
                           ) AS position
                    FROM price_snapshots
                    WHERE language = 'FR' AND condition = 'NM'
                      AND scryfall_id IN ({placeholders})
                ) AS latest WHERE position = 1""",
            chunk,
        ).fetchall()
        for row in rows:
            result[(row["scryfall_id"], bool(row["foil"]))] = {
                "observed_at": row["observed_at"], "price_eur": row["price_eur"],
            }
    return result


def _record_stock_snapshots(connection, records, observed_at):
    if records:
        connection.executemany(
            """INSERT INTO price_snapshots(
                   scryfall_id, foil, language, condition, snapshot_date, observed_at, price_eur
               ) VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(scryfall_id, foil, language, condition, snapshot_date)
               DO UPDATE SET observed_at = excluded.observed_at, price_eur = excluded.price_eur""",
            [(str(card_id), int(bool(foil)), "FR", "NM", observed_at[:10], observed_at,
              validate_monetary_amount(price, "Le prix du relevé"))
             for card_id, foil, price in records],
        )


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


def get_variant_price_histories_batch(variants):
    keys = list(dict.fromkeys(
        (str(card_id), bool(foil), str(language), str(condition))
        for card_id, foil, language, condition in variants
    ))
    points = {key: [] for key in keys}
    ids = list(dict.fromkeys(key[0] for key in keys))
    if not ids:
        return points
    with _database() as connection:
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            placeholders = ", ".join("?" for _ in chunk)
            rows = connection.execute(
                f"""SELECT scryfall_id, foil, language, condition, snapshot_date, observed_at, price_eur
                    FROM price_snapshots WHERE scryfall_id IN ({placeholders})
                    ORDER BY snapshot_date""",
                chunk,
            ).fetchall()
            for row in rows:
                key = (row["scryfall_id"], bool(row["foil"]), row["language"], row["condition"])
                if key in points:
                    points[key].append({
                        "snapshot_date": row["snapshot_date"],
                        "observed_at": row["observed_at"],
                        "price_eur": row["price_eur"],
                    })
    return points


def get_price_points_batch(variants):
    histories = get_variant_price_histories_batch(variants)
    return {
        key: [(row["snapshot_date"], row["price_eur"]) for row in rows]
        for key, rows in histories.items()
    }


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
