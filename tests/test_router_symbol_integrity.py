"""Guard against NameError bugs hidden behind star imports in router packages.

Router part-packages (materials_parts, homework_parts) rely on
``from .common import *`` style imports. A name referenced in a rarely-hit
branch (e.g. a success-path response assembly) can be missing without any
import-time failure, and only explodes as a runtime NameError in production.
This test resolves every star import for real and asserts that every loaded
name in each module is defined somewhere reachable.
"""

from __future__ import annotations

import ast
import builtins
import importlib
import os
import pathlib
import unittest

os.environ.setdefault("DB_ENGINE", "sqlite")

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
ROUTER_PACKAGES = (
    "classroom_app/routers/materials_parts",
    "classroom_app/routers/homework_parts",
)


def _collect_defined_names(tree: ast.AST) -> tuple[set[str], list[str]]:
    defined = set(dir(builtins))
    star_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    star_modules.append(node.module or "")
                else:
                    defined.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                defined.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)
        elif isinstance(node, ast.Global):
            defined.update(node.names)
    return defined, star_modules


def _star_exported_names(module_name: str, package: str) -> set[str]:
    if module_name.startswith("."):
        module = importlib.import_module(module_name, package)
    else:
        try:
            module = importlib.import_module(f".{module_name}", package)
        except ModuleNotFoundError:
            module = importlib.import_module(module_name)
    exported = getattr(module, "__all__", None)
    if exported is None:
        exported = [name for name in dir(module) if not name.startswith("_")]
    return set(exported)


class RouterSymbolIntegrityTests(unittest.TestCase):
    def test_all_loaded_names_resolve(self):
        problems: list[str] = []
        for package_path in ROUTER_PACKAGES:
            base = PROJECT_ROOT / package_path
            package = package_path.replace("/", ".")
            for path in sorted(base.glob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                defined, star_modules = _collect_defined_names(tree)
                for star_module in star_modules:
                    defined.update(_star_exported_names(star_module, package))
                for node in ast.walk(tree):
                    if (
                        isinstance(node, ast.Name)
                        and isinstance(node.ctx, ast.Load)
                        and node.id not in defined
                    ):
                        problems.append(
                            f"{package_path}/{path.name}:{node.lineno}: "
                            f"name '{node.id}' is not defined via imports, "
                            "star exports, or local assignment"
                        )
        self.assertEqual(problems, [], "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
