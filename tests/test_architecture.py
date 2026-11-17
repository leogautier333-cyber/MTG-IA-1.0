import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_PATHS = ("database", "services", "agents", "ai_engine.py", "discord_bot.py", "scryfall.py")
PRIVATE_OWNERS = {"storage", "market_data", "stock_advisor", "inventory", "snapshot", "backup"}


def _sources(paths):
    for name in paths:
        path = ROOT / name
        yield from ([path] if path.is_file() else sorted(path.rglob("*.py")))


class ArchitectureTests(unittest.TestCase):
    def test_core_layers_do_not_depend_on_streamlit_or_its_cache_facade(self):
        offenders = []
        for path in _sources(CORE_PATHS):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [node.module or ""]
                if any(name.split(".")[0] in {"streamlit", "data_manager"} for name in names):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_modules_outside_the_database_package_use_public_interfaces_only(self):
        offenders = []
        for path in _sources(("services", "agents", "modules", "ai_engine.py", "discord_bot.py",
                              "app.py", "data_manager.py", "performance_view.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Attribute) and node.attr.startswith("_")
                        and not node.attr.startswith("__")
                        and isinstance(node.value, ast.Name) and node.value.id in PRIVATE_OWNERS):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.value.id}.{node.attr}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
