"""Export et restauration complète de la base (utilisés par database.backup)."""
import json
from datetime import datetime, timezone

from database.storage import _database

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
    "alert_evaluations": (
        "alert_id", "horizon_days", "target_date", "observed_date",
        "reference_price", "observed_price", "change_pct", "direction_correct",
        "evaluated_at",
    ),
    "metadata": ("key", "value"),
}
BACKUP_FORMAT_VERSION = 1
OPTIONAL_TABLES = {"price_alerts"}


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
    tables = {name: tables.get(name, []) for name in BACKUP_TABLE_COLUMNS}
    for table_name, columns in BACKUP_TABLE_COLUMNS.items():
        rows = tables[table_name]
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not set(columns).issubset(row)
            for row in rows
        ):
            raise ValueError(f"Données invalides dans la table {table_name}.")

    delete_order = (
        "alert_evaluations", "alerts", "price_alerts", "bulk_prints", "source_health",
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
