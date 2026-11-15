import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class StreamlitSecretsTests(unittest.TestCase):
    def run_app(self, contents, script):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secrets = root / "secrets.toml"
            if contents is not None:
                secrets.write_text(contents, encoding="utf-8")
            env = os.environ.copy()
            for name in (
                "DATABASE_URL", "MTG_DATABASE_URL", "GITHUB_ACTIONS",
                "GITHUB_DISPATCH_TOKEN", "DISCORD_WEBHOOK_URL",
            ):
                env.pop(name, None)
            env["MTG_DATABASE_PATH"] = str(root / "test.sqlite3")
            env["TEST_SECRETS_PATH"] = str(secrets)
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=project, env=env, capture_output=True, text=True, timeout=90,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_application_loads_root_secrets_before_using_services(self):
        self.run_app(
            'GITHUB_DISPATCH_TOKEN = "test-token"\n'
            'DISCORD_WEBHOOK_URL = "https://discord.invalid/test"\n',
            """
import os
from unittest.mock import Mock, patch
from streamlit import config
from streamlit.testing.v1 import AppTest

config.set_option("secrets.files", [os.environ["TEST_SECRETS_PATH"]])
response = Mock(status_code=200)
response.json.return_value = {"workflow_runs": []}
with patch("requests.get", return_value=response):
    app = AppTest.from_file("app.py", default_timeout=60).run()
assert not app.exception
from services import github_actions, phase_one_monitor
assert github_actions.is_configured()
assert os.getenv(phase_one_monitor.DISCORD_WEBHOOK_URL) == "https://discord.invalid/test"
assert not any("Pour activer ces boutons" in item.value for item in app.info)
""",
        )

    def test_database_secret_is_available_before_storage_connects(self):
        self.run_app(
            'DATABASE_URL = "postgresql://test:fake@example.invalid/stock"\n',
            """
import os
from unittest.mock import patch
from streamlit import config
from streamlit.testing.v1 import AppTest

config.set_option("secrets.files", [os.environ["TEST_SECRETS_PATH"]])
with patch("psycopg.connect", side_effect=RuntimeError("test connection")) as connect:
    AppTest.from_file("app.py", default_timeout=60).run()
from database import storage
assert storage.get_database_backend() == "postgres"
assert connect.called
""",
        )

    def test_no_secrets_file_still_allows_local_application(self):
        self.run_app(
            None,
            """
import os
from streamlit import config
from streamlit.testing.v1 import AppTest

config.set_option("secrets.files", [os.environ["TEST_SECRETS_PATH"]])
app = AppTest.from_file("app.py", default_timeout=60).run()
assert not app.exception
from database import storage
assert storage.get_database_backend() == "sqlite"
""",
        )

    def test_invalid_secrets_file_is_reported(self):
        self.run_app(
            'GITHUB_DISPATCH_TOKEN = "unterminated\n',
            """
import os
from streamlit import config
from streamlit.testing.v1 import AppTest

config.set_option("secrets.files", [os.environ["TEST_SECRETS_PATH"]])
app = AppTest.from_file("app.py", default_timeout=60).run()
assert app.exception
""",
        )

    def test_scryfall_timeout_is_shown_to_user(self):
        self.run_app(
            None,
            """
from unittest.mock import patch
import requests
from streamlit.testing.v1 import AppTest

with patch("requests.get", side_effect=requests.Timeout("test")):
    app = AppTest.from_file("app.py", default_timeout=60).run()
    search = next(item for item in app.text_input if item.label.startswith("Nom de la carte"))
    search.input("Test").run()
assert not app.exception
assert any("Scryfall indisponible" in item.value for item in app.error)
""",
        )


if __name__ == "__main__":
    unittest.main()
