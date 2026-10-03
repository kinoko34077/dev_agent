#!/usr/bin/env python3
"""Read existing OpenRouter key/account/model state without changing authority."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.openrouter.introspection import OpenRouterIntrospectionClient, OpenRouterIntrospectionError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--required-parameter",
        action="append",
        default=[],
        help="Require a model supported_parameter for zero-price candidate discovery; may be repeated",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    client = OpenRouterIntrospectionClient()
    try:
        state = client.read_state(required_parameters=set(args.required_parameter))
    except ProviderError as exc:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "PROVIDER_FAILED",
                    "category": exc.category,
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2 if exc.category == "authentication" else 1
    except OpenRouterIntrospectionError as exc:
        print(json.dumps({"schema_version": 1, "status": "OBSERVATION_FAILED", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"status": "OBSERVED", **state}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
