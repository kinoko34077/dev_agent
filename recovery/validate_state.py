"""Minimal JSON state validation for recovery use; intentionally no v2 imports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REQUIRED_KEYS = frozenset({"task_id", "status"})


def validate_state(path: str | Path) -> tuple[bool, str]:
    state_path = Path(path)
    try:
        with state_path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"cannot read JSON state: {exc}"
    if not isinstance(value, dict):
        return False, "state root must be an object"
    missing = sorted(REQUIRED_KEYS - value.keys())
    if missing:
        return False, f"missing required keys: {', '.join(missing)}"
    return True, "state shape is readable"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a JSON task state")
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    ok, message = validate_state(args.path)
    print(f"{'PASS' if ok else 'FAIL'}: {message}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
