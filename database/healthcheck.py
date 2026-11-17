import sys

from database import storage


def check_database():
    backend = storage.get_database_backend()
    storage.initialize_database()
    with storage._database() as connection:
        result = connection.execute("SELECT 1 AS ready").fetchone()
        if not result or result["ready"] != 1:
            raise storage.DatabaseConnectionError("PostgreSQL health query returned an unexpected result.")

        if backend == "postgres":
            rows = connection.execute(
                """SELECT table_name AS name FROM information_schema.tables
                   WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"""
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        present_tables = {row["name"] for row in rows}
        missing_tables = storage.REQUIRED_TABLES - present_tables
        if missing_tables:
            raise storage.DatabaseConnectionError(
                "Database schema is incomplete; missing table(s): "
                + ", ".join(sorted(missing_tables))
            )
        inconsistent_entries = storage.find_inventory_inconsistencies(connection)
        invalid_snapshots = storage.find_invalid_price_snapshots(connection)
    return {
        "backend": backend, "table_count": len(present_tables),
        "inconsistent_inventory_entries": inconsistent_entries,
        "invalid_price_snapshots": invalid_snapshots,
    }


def main():
    try:
        result = check_database()
    except (storage.DatabaseConfigurationError, storage.DatabaseConnectionError) as error:
        print(f"Database healthcheck failed: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(
            f"Database schema initialization failed ({type(error).__name__}). "
            "Check DATABASE_URL, database permissions and migration logs.",
            file=sys.stderr,
        )
        return 1
    print(f"Database ready: backend={result['backend']}, tables={result['table_count']}")
    if result["inconsistent_inventory_entries"]:
        print(
            "Warning: inventory columns differ from their JSON payload for entry_id(s): "
            + ", ".join(str(entry) for entry in result["inconsistent_inventory_entries"][:20])
            + ". No data was modified; review these rows manually.",
            file=sys.stderr,
        )
    if result["invalid_price_snapshots"]:
        print(
            "Warning: invalid stored price snapshot id(s): "
            + ", ".join(str(item) for item in result["invalid_price_snapshots"][:20])
            + ". No data was modified; review these rows manually.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())