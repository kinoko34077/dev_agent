from __future__ import annotations

import ast
from pathlib import Path


def _imports_route(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "src.dev_agent.providers.host_dispatch"
        and any(alias.name == "route_through_host" for alias in node.names)
        for node in ast.walk(tree)
    )


def test_standard_host_process_compositions_use_shared_route_boundary():
    root = Path(__file__).resolve().parents[2]

    assert _imports_route(root / "scripts" / "devfarm_commander.py")
    assert _imports_route(root / "scripts" / "devfarm_supervisor.py")
    assert _imports_route(root / "scripts" / "devfarm_worker.py")
