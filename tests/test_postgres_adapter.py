import unittest
import io
import os
from contextlib import redirect_stderr
from unittest.mock import patch

from database import storage
from database.healthcheck import main as database_healthcheck


class FakeCursor:
    def __init__(self, statements):
        self.statements = statements

    def executemany(self, query, parameters):
        self.statements.append((query, list(parameters)))


class FakeConnection:
    def __init__(self):
        self.statements = []

    def execute(self, query, parameters=()):
        self.statements.append((query, parameters))
        return FakeCursor(self.statements)

    def cursor(self):
        return FakeCursor(self.statements)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class PostgresAdapterTests(unittest.TestCase):
    def test_missing_database_url_in_github_actions_fails_clearly(self):
        with patch.object(storage, "DATABASE_URL", None), patch.dict(os.environ, {
            "DATABASE_URL": "", "MTG_DATABASE_URL": "", "GITHUB_ACTIONS": "true",
        }):
            with self.assertRaisesRegex(storage.DatabaseConfigurationError, "DATABASE_URL is required"):
                storage._connect()

    def test_ci_healthcheck_stops_with_actionable_missing_secret_message(self):
        error_output = io.StringIO()
        with patch.object(storage, "DATABASE_URL", None), patch.dict(os.environ, {
            "DATABASE_URL": "", "MTG_DATABASE_URL": "", "GITHUB_ACTIONS": "true",
        }), redirect_stderr(error_output):
            exit_code = database_healthcheck()
        self.assertEqual(exit_code, 1)
        self.assertIn("DATABASE_URL is required", error_output.getvalue())

    def test_invalid_database_url_is_rejected_without_echoing_secret(self):
        with patch.object(storage, "DATABASE_URL", "not-a-postgresql-uri"), patch.dict(os.environ, {
            "DATABASE_URL": "", "MTG_DATABASE_URL": "", "GITHUB_ACTIONS": "false",
        }):
            with self.assertRaisesRegex(storage.DatabaseConfigurationError, "PostgreSQL URI"):
                storage._connect()

    def test_postgres_connection_failure_is_actionable_and_does_not_leak_url(self):
        secret_uri = "postgresql://user:supersecret@example.invalid/db"
        with patch.object(storage, "DATABASE_URL", secret_uri), \
             patch("psycopg.connect", side_effect=TimeoutError("socket timed out")):
            with self.assertRaises(storage.DatabaseConnectionError) as raised:
                storage._connect()
        self.assertIn("15-second timeout", str(raised.exception))
        self.assertNotIn("supersecret", str(raised.exception))

    def test_postgres_connection_uses_configured_dsn_and_named_rows(self):
        fake_connection = FakeConnection()
        with patch.object(storage, "DATABASE_URL", "postgresql://example.invalid/db"), \
             patch("psycopg.connect", return_value=fake_connection) as connect:
            connection = storage._connect()

        self.assertEqual(connection.dialect, "postgres")
        self.assertEqual(connect.call_args.args[0], "postgresql://example.invalid/db")
        self.assertEqual(connect.call_args.kwargs["connect_timeout"], 15)

    def test_postgres_adapter_translates_placeholders_and_migrations(self):
        fake_connection = FakeConnection()
        connection = storage._DatabaseConnection(fake_connection, "postgres")

        connection.execute("SELECT value FROM metadata WHERE key = ?", ("key",))
        connection.executemany("INSERT INTO metadata(key) VALUES (?)", [("a",), ("b",)])
        connection.executescript("CREATE TABLE first_table(id INTEGER); CREATE INDEX first_index ON first_table(id);")

        self.assertEqual(fake_connection.statements[0][0], "SELECT value FROM metadata WHERE key = %s")
        self.assertEqual(fake_connection.statements[1][0], "INSERT INTO metadata(key) VALUES (%s)")
        self.assertIn("CREATE TABLE first_table", fake_connection.statements[2][0])
        self.assertIn("CREATE INDEX first_index", fake_connection.statements[3][0])


if __name__ == "__main__":
    unittest.main()