import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
IMPORT_NAMES = {"psycopg-binary": "psycopg"}


def _requirement_modules(text):
    modules = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[\[<>=!~ ]", line, maxsplit=1)[0].lower()
        modules.add(IMPORT_NAMES.get(name, name))
    return modules


def _installed_modules(workflow_text):
    install = re.search(r"python -m pip install (.+)", workflow_text).group(1)
    modules = set()
    for argument in re.findall(r'(?:-r\s+(\S+))|"([^"]+)"|(\S+)', install):
        requirements_file, quoted, bare = argument
        if requirements_file:
            modules |= _requirement_modules((ROOT / requirements_file).read_text(encoding="utf-8"))
        elif (quoted or bare) and not (quoted or bare).startswith(("-", "&")):
            modules |= _requirement_modules(quoted or bare)
    return modules


def _module_path(name):
    parts = name.split(".")
    for candidate in (ROOT.joinpath(*parts).with_suffix(".py"), ROOT.joinpath(*parts, "__init__.py")):
        if candidate.exists():
            return candidate
    return None


def _is_local(name):
    return _module_path(name) is not None or ROOT.joinpath(*name.split(".")).is_dir()


def _third_party_imports(entry_module):
    pending, seen, external = [entry_module], set(), set()
    while pending:
        name = pending.pop()
        path = _module_path(name)
        if name in seen or path is None:
            continue
        seen.add(name)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
            else:
                continue
            for module in imported:
                if _is_local(module):
                    pending.append(module)
                elif (module.split(".")[0] not in sys.stdlib_module_names
                      and not _is_local(module.split(".")[0])):
                    external.add(module.split(".")[0])
    return external


class WorkflowDependencyTests(unittest.TestCase):
    def test_python_workflows_install_every_runtime_dependency_they_import(self):
        checked = 0
        for workflow in sorted(WORKFLOWS.glob("*.yml")):
            text = workflow.read_text(encoding="utf-8")
            entry = re.search(r"run: python -m ((?!pip|unittest)[\w.]+)", text)
            if not entry:
                continue
            checked += 1
            with self.subTest(workflow=workflow.name):
                missing = _third_party_imports(entry.group(1)) - _installed_modules(text)
                self.assertEqual(missing, set(), f"{workflow.name} n'installe pas {sorted(missing)}")
        self.assertGreaterEqual(checked, 4)


if __name__ == "__main__":
    unittest.main()
