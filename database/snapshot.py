"""Export et restauration complète de la base (utilisés par database.backup)."""
import json
from datetime import datetime, timezone

import ijson

from database.storage import SCHEMA_VERSION, _database, _reset_inventory_history

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
    "price_alerts": ("alert_id", "status", "created_at", "payload"),
    "inventory_events": ("occurred_at", "old_payload", "new_payload"),
    "alert_evaluations": (
        "alert_id", "horizon_days", "target_date", "observed_date",
        "reference_price", "observed_price", "change_pct", "direction_correct",
        "evaluated_at",
    ),
    "metadata": ("key", "value"),
}
BACKUP_FORMAT_VERSION = 1
OPTIONAL_TABLES = {"price_alerts", "inventory_events"}
DELETE_ORDER = (
    "alert_evaluations", "alerts", "price_alerts", "inventory_events", "bulk_prints", "source_health",
    "card_demand_snapshots", "price_snapshots", "watchlist", "inventory", "metadata",
)


def _validate_schema_version(row):
    if row["key"] == "schema_version":
        try:
            version = int(row["value"])
        except (TypeError, ValueError):
            raise ValueError("Version de schéma invalide dans la sauvegarde.") from None
        if version < 0 or version > SCHEMA_VERSION:
            raise ValueError("La sauvegarde nécessite une version plus récente de l'application.")


def _begin_restore(connection):
    if connection.dialect == "postgres":
        connection.execute("LOCK TABLE inventory IN SHARE ROW EXCLUSIVE MODE")
    connection.execute("UPDATE inventory_history_control SET enabled = 0 WHERE id = 1")
    for table_name in DELETE_ORDER:
        connection.execute(f"DELETE FROM {table_name}")


def _insert_rows(connection, table_name, rows):
    if rows:
        columns = BACKUP_TABLE_COLUMNS[table_name]
        placeholders = ", ".join("?" for _ in columns)
        column_sql = ", ".join(columns)
        connection.executemany(
            f"INSERT INTO {table_name} ({column_sql}) VALUES ({placeholders})",
            [tuple(row[column] for column in columns) for row in rows],
        )


def _finish_restore(connection, has_inventory_events):
    if not has_inventory_events:
        _reset_inventory_history(connection)
    connection.execute(
        """INSERT INTO metadata(key, value) VALUES ('schema_version', ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        (str(SCHEMA_VERSION),),
    )
    connection.execute("UPDATE inventory_history_control SET enabled = 1 WHERE id = 1")


def _begin_snapshot(connection):
    if connection.dialect == "postgres":
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    else:
        connection.execute("BEGIN")


def _select_table(connection, table_name, columns, stream=False):
    order_by = {
        "inventory": "entry_id",
        "watchlist": "entry_id",
        "price_snapshots": "snapshot_date, snapshot_id",
        "inventory_events": "event_id",
    }.get(table_name)
    ordering = f" ORDER BY {order_by}" if order_by else ""
    query = f"SELECT {', '.join(columns)} FROM {table_name}{ordering}"
    if stream and connection.dialect == "postgres":
        cursor = connection.connection.cursor(name=f"backup_{table_name}")
        cursor.execute(query)
        return cursor
    return connection.execute(query)


def export_database_snapshot():
    with _database() as connection:
        _begin_snapshot(connection)
        tables = {}
        for table_name, columns in BACKUP_TABLE_COLUMNS.items():
            tables[table_name] = [
                dict(row) for row in _select_table(connection, table_name, columns).fetchall()
            ]
    return {
        "format": "magic-trading-sql-backup",
        "format_version": BACKUP_FORMAT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tables": tables,
    }


def iter_database_snapshot_json(batch_size=500):
    if batch_size < 1:
        raise ValueError("La taille du lot doit être positive.")
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _database() as connection:
        _begin_snapshot(connection)
        header = {
            "format": "magic-trading-sql-backup",
            "format_version": BACKUP_FORMAT_VERSION,
            "created_at": created_at,
        }
        yield (json.dumps(header, ensure_ascii=False, separators=(",", ":"))[:-1] + ',"tables":{').encode("utf-8")
        for table_index, (table_name, columns) in enumerate(BACKUP_TABLE_COLUMNS.items()):
            if table_index:
                yield b","
            yield (json.dumps(table_name) + ":[").encode("utf-8")
            cursor = _select_table(connection, table_name, columns, stream=True)
            try:
                first = True
                while True:
                    rows = cursor.fetchmany(batch_size)
                    if not rows:
                        break
                    for row in rows:
                        if not first:
                            yield b","
                        first = False
                        yield json.dumps(dict(row), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            finally:
                cursor.close()
            yield b"]"
        yield b"}}"


def restore_database_snapshot(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("format") != "magic-trading-sql-backup":
        raise ValueError("Format de sauvegarde inconnu.")
    if snapshot.get("format_version") != BACKUP_FORMAT_VERSION:
        raise ValueError("Version de sauvegarde non prise en charge.")
    tables = snapshot.get("tables")
    if (
        not isinstance(tables, dict)
        or not set(BACKUP_TABLE_COLUMNS) - OPTIONAL_TABLES <= set(tables) <= set(BACKUP_TABLE_COLUMNS)
    ):
        raise ValueError("La sauvegarde est incomplète ou contient un schéma inattendu.")
    has_inventory_events = "inventory_events" in tables
    tables = {name: tables.get(name, []) for name in BACKUP_TABLE_COLUMNS}
    for table_name, columns in BACKUP_TABLE_COLUMNS.items():
        rows = tables[table_name]
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not set(columns).issubset(row)
            for row in rows
        ):
            raise ValueError(f"Données invalides dans la table {table_name}.")
    for row in tables["metadata"]:
        _validate_schema_version(row)

    with _database() as connection:
        _begin_restore(connection)
        for table_name in BACKUP_TABLE_COLUMNS:
            _insert_rows(connection, table_name, tables[table_name])
        _finish_restore(connection, has_inventory_events)
    return {table_name: len(rows) for table_name, rows in tables.items()}


def restore_database_snapshot_stream(source, batch_size=500):
    """Restaure l'export séquentiel en mémoire bornée, dans une seule transaction."""
    if batch_size < 1:
        raise ValueError("La taille du lot doit être positive.")
    counts = dict.fromkeys(BACKUP_TABLE_COLUMNS, 0)
    seen_tables = set()
    headers = {}
    table_order = list(BACKUP_TABLE_COLUMNS)
    last_table_index = -1
    table_name = None
    rows = []
    builder = None
    depth = 0
    root_started = tables_started = False

    with _database() as connection:
        _begin_restore(connection)
        for prefix, event, value in ijson.parse(source, use_float=True):
            if builder is not None:
                builder.event(event, value)
                if event in ("start_map", "start_array"):
                    depth += 1
                elif event in ("end_map", "end_array"):
                    depth -= 1
                if depth == 0:
                    row = builder.value
                    builder = None
                    if not set(BACKUP_TABLE_COLUMNS[table_name]).issubset(row):
                        raise ValueError(f"Données invalides dans la table {table_name}.")
                    if table_name == "metadata":
                        _validate_schema_version(row)
                    rows.append(row)
                    counts[table_name] += 1
                    if len(rows) >= batch_size:
                        _insert_rows(connection, table_name, rows)
                        rows.clear()
                continue
            if prefix == "" and event == "start_map":
                root_started = True
            elif prefix in ("format", "format_version") and event in ("string", "number"):
                if prefix in headers:
                    raise ValueError("En-tête de sauvegarde en double.")
                headers[prefix] = value
            elif prefix == "tables" and event == "start_map":
                if tables_started:
                    raise ValueError("Tables de sauvegarde en double.")
                tables_started = True
            elif prefix == "tables" and event == "map_key":
                if value not in BACKUP_TABLE_COLUMNS or value in seen_tables:
                    raise ValueError("La sauvegarde contient une table inattendue ou en double.")
                index = table_order.index(value)
                if index <= last_table_index:
                    raise ValueError("Ordre des tables de sauvegarde invalide.")
                last_table_index = index
                table_name = value
                seen_tables.add(value)
            elif table_name and prefix == f"tables.{table_name}" and event == "start_array":
                pass
            elif table_name and prefix == f"tables.{table_name}.item" and event == "start_map":
                builder = ijson.common.ObjectBuilder()
                builder.event(event, value)
                depth = 1
            elif table_name and prefix == f"tables.{table_name}" and event == "end_array":
                _insert_rows(connection, table_name, rows)
                rows.clear()
                table_name = None
            elif table_name and prefix.startswith(f"tables.{table_name}"):
                raise ValueError(f"Données invalides dans la table {table_name}.")

        if (not root_started or not tables_started or table_name is not None
                or not set(BACKUP_TABLE_COLUMNS) - OPTIONAL_TABLES <= seen_tables
                or headers.get("format") != "magic-trading-sql-backup"
                or headers.get("format_version") != BACKUP_FORMAT_VERSION):
            raise ValueError("Format ou contenu de sauvegarde invalide.")
        _finish_restore(connection, "inventory_events" in seen_tables)
    return counts
