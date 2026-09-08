"""Small JSON worker used for hard-bounded Tool handler execution.

The worker accepts an importable ``module:qualname`` and one JSON object on
stdin.  It emits exactly one JSON result on stdout; handler stdout is moved to
stderr so diagnostic prints cannot corrupt the protocol.
"""

from __future__ import annotations

from contextlib import redirect_stdout
import importlib
import json
from pathlib import Path
import sys
from typing import Any

# The worker is launched by absolute script path, so Python's default
# ``sys.path[0]`` is this tools directory rather than the repository root.
# Make importable test/application handlers available without depending on a
# shell-specific PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def _resolve(reference: str):
    try:
        module_name, qualname = reference.split(":", 1)
        if not module_name or not qualname or "<locals>" in qualname:
            raise ValueError("handler reference must name a top-level callable")
        value = importlib.import_module(module_name)
        for part in qualname.split("."):
            value = getattr(value, part)
        if not callable(value):
            raise TypeError("handler reference is not callable")
        return value
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        raise RuntimeError(f"cannot resolve handler: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print(json.dumps({"ok": False, "error": {"type": "protocol", "message": "one handler reference is required"}}))
        return 2
    try:
        arguments = json.load(sys.stdin)
        if not isinstance(arguments, dict):
            raise TypeError("handler arguments must be an object")
        handler = _resolve(args[0])
        # A handler may print for its own diagnostics.  Keep the stdout channel
        # reserved for the parent/worker protocol.
        with redirect_stdout(sys.stderr):
            result: Any = handler(arguments)
        if not isinstance(result, dict):
            raise TypeError("tool handler must return a dict")
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, separators=(",", ":")))
        return 0
    except BaseException as exc:
        print(json.dumps({"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}, ensure_ascii=False, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
