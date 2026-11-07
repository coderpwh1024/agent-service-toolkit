import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize(
    "layer", ["services", "agents", "repositories", "core", "models", "schemas"]
)
def test_business_layers_do_not_depend_on_http_or_application_assembly(layer: str) -> None:
    forbidden = {"fastapi", "app.api", "app.main", "app.lifecycle"}
    for path in (ROOT / "app" / layer).rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            assert not any(
                module == target or module.startswith(target + ".")
                for module in modules
                for target in forbidden
            ), f"{path.relative_to(ROOT)} imports a transport or assembly module"


def test_repositories_do_not_depend_on_business_services() -> None:
    for path in (ROOT / "app" / "repositories").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("app.services"), str(path)
