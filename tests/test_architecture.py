import ast
from pathlib import Path


def test_inner_layers_have_no_outward_dependencies():
    source = Path(__file__).parents[1] / "src" / "relay"
    for layer in ("domain", "application"):
        for path in (source / layer).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                forbidden = (
                    "fastapi",
                    "sqlalchemy",
                    "pydantic",
                    "starlette",
                    "relay.infrastructure",
                    "relay.presentation",
                )
                if layer == "domain":
                    forbidden += ("relay.application",)
                assert all(not module.startswith(forbidden) for module in modules), path
