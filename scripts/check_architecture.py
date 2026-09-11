"""Stdlib-only dependency direction and barrel-import check for v2."""

from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def _module_name(path: Path) -> str:
    relative = path.relative_to(ROOT)
    if relative.parts[0] == "src":
        return ".".join(relative.with_suffix("").parts[1:])
    if relative.parts[0] == "recovery":
        return ".".join(relative.with_suffix("").parts)
    if relative.parts[0] == "scripts":
        return ".".join(relative.with_suffix("").parts)
    raise ValueError(f"unsupported architecture path: {path}")


def _normalize(name: str | None) -> str:
    if not name:
        return ""
    return name[4:] if name.startswith("src.") else name


def _from_import_targets(current: str, node: ast.ImportFrom) -> tuple[str, ...]:
    package = current.split(".")[:-1]
    prefix_length = max(0, len(package) - node.level + 1) if node.level else 0
    base = package[:prefix_length]
    if node.module:
        base.extend(node.module.split("."))
        return (_normalize(".".join(base)),)
    return tuple(_normalize(".".join((*base, alias.name))) for alias in node.names)


def _imports(path: Path) -> tuple[str, tuple[str, ...]]:
    current = _module_name(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend(_normalize(alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            targets.extend(_from_import_targets(current, node))
    return current, tuple(targets)


def _violation(current: str, target: str) -> str | None:
    if current.startswith("dev_agent.domain") and target.startswith(("dev_agent.runtime", "dev_agent.providers", "dev_agent.resources", "dev_agent.state")):
        return "domain must not import runtime/providers/resources/state"
    if current.startswith("dev_agent.providers") and target.startswith("dev_agent.runtime"):
        return "Provider layer must not import runtime/controller"
    if current.startswith("dev_agent.resources") and target.startswith("dev_agent.runtime"):
        return "Resource layer must not import runtime/controller"
    if current.startswith("dev_agent.state") and target.startswith("dev_agent.scheduler"):
        return "State layer must not import concrete Scheduler"
    if current.startswith("recovery") and target.startswith("dev_agent.runtime"):
        return "Recovery must remain independent from runtime/controller"
    if current.startswith("scripts.devfarm") and target.startswith(("dev_agent.state", "dev_agent.scheduler")):
        return "DevFarm must not own production durable State/Scheduler"
    if current.startswith("dev_agent.") and not current.endswith(".__init__") and target in {
        "dev_agent.providers",
        "dev_agent.intelligence",
        "dev_agent.resources",
        "dev_agent.state",
    }:
        return "internal modules must use leaf imports instead of package barrels"
    return None


def check(paths: tuple[Path, ...] | None = None) -> list[str]:
    if paths is None:
        paths = tuple((ROOT / "src" / "dev_agent").rglob("*.py")) + tuple(
            path for path in (ROOT / "recovery").rglob("*.py")
        ) + tuple(
            path for path in (ROOT / "scripts").glob("devfarm*.py")
        )
    violations: list[str] = []
    for path in sorted(paths):
        try:
            current, targets = _imports(path)
        except (OSError, SyntaxError) as exc:
            violations.append(f"{path.relative_to(ROOT)}: unable to parse: {exc}")
            continue
        for target in targets:
            reason = _violation(current, target)
            if reason:
                violations.append(f"{path.relative_to(ROOT)} -> {target}: {reason}")
    return violations


def main() -> int:
    violations = check()
    if violations:
        print("ARCHITECTURE_FAIL")
        print("\n".join(violations))
        return 1
    print("ARCHITECTURE_PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
