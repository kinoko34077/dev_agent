"""Probe Groq's model catalog without sending an inference request.

The probe is diagnostic only.  It reports whether the credentials can list
models and whether a requested model is present; it never promotes a Provider
or changes Gate evidence.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.groq import GroqHttpProvider


def probe(*, model: str | None = None, timeout_seconds: float = 30.0) -> dict[str, object]:
    provider = GroqHttpProvider(model=model or "models-probe", timeout_seconds=timeout_seconds)
    try:
        models = provider.list_models()
    except ProviderError as exc:
        if exc.category == "authentication":
            diagnosis = "credentials_missing"
            status = "blocked_external"
        elif exc.category == "authorization":
            diagnosis = "models_endpoint_permission_denied"
            status = "failed"
        elif exc.category == "rate_limit":
            diagnosis = "models_endpoint_rate_limited"
            status = "blocked_external"
        else:
            diagnosis = "models_endpoint_unavailable"
            status = "failed"
        return {
            "schema_version": 1,
            "probe": "groq_models",
            "provider": "groq",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "category": exc.category,
            "diagnosis": diagnosis,
            "message": str(exc),
        }
    model_ids = [item["id"] for item in models]
    if model is None:
        return {
            "schema_version": 1,
            "probe": "groq_models",
            "provider": "groq",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "status": "ok",
            "model_status": "not_requested",
            "diagnosis": "models_endpoint_ok",
            "model_count": len(model_ids),
            "model_ids": model_ids,
        }
    listed = model in model_ids
    return {
        "schema_version": 1,
        "probe": "groq_models",
        "provider": "groq",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "requested_model": model,
        "status": "ok" if listed else "model_not_listed",
        "model_status": "listed" if listed else "not_listed",
        "diagnosis": "models_endpoint_ok_inference_permission_unproven" if listed else "requested_model_not_listed",
        "model_count": len(model_ids),
        "model_ids": model_ids,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    output = probe(model=args.model, timeout_seconds=args.timeout_seconds)
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output is not None:
        destination = args.output.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered + "\n", encoding="utf-8")
    return 0 if output["status"] == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
