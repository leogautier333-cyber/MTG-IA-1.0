import unittest
from unittest.mock import patch

from database import storage


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