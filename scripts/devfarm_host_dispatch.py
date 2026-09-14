"""Process one bounded Host-owned Provider dispatch.

The command is intentionally a one-shot adapter, not a proxy or scheduler.
The request contains only a Host-admitted identity, a ModelRequest, and the
egress-manifest digest.  Credentials, endpoints, and Provider selection are
resolved in this Host process from its configured environment and existing
qualification/billing admission.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.devfarm_resource_pool import admit_resource_pool, compose_resource_pool
from src.dev_agent.domain.protocol import ModelResponse
from src.dev_agent.operation import OperationProviderBinding, configured_provider_pool_from_environment
from src.dev_agent.providers.base import ModelProvider, ProviderError
from src.dev_agent.providers.host_dispatch import (
    HostDispatchEnvelope,
    HostProcessExecutor,
    HostProviderDispatch,
)
from src.dev_agent.resources.qualification import QualificationResolver


MAX_REQUEST_BYTES = 512 * 1024
MAX_RESPONSE_BYTES = 512 * 1024


class HostDispatchRuntimeError(ValueError):
    """The Host one-shot dispatch request cannot be safely processed."""


def create_host_process_executor(
    request_dir: str | Path,
    *,
    timeout_seconds: float = 120.0,
) -> HostProcessExecutor:
    """Build the repository's static one-shot Host runtime command.

    Planner, Reviewer, Critic, and Worker callers share this exact command
    composition.  It is intentionally derived from this checked-in adapter,
    not from a caller-provided URL or arbitrary shell string.
    """

    return HostProcessExecutor(
        (sys.executable, str(Path(__file__).resolve())),
        request_dir=request_dir,
        timeout_seconds=timeout_seconds,
    )


def _read_envelope(path: str | Path) -> HostDispatchEnvelope:
    target = Path(path).resolve()
    try:
        if target.stat().st_size > MAX_REQUEST_BYTES:
            raise HostDispatchRuntimeError("Host dispatch request exceeds its size bound")
        value = json.loads(target.read_text(encoding="utf-8"))
    except HostDispatchRuntimeError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HostDispatchRuntimeError("Host dispatch request is unreadable") from exc
    if not isinstance(value, dict):
        raise HostDispatchRuntimeError("Host dispatch request must be an object")
    try:
        return HostDispatchEnvelope.from_dict(value)
    except (TypeError, ValueError) as exc:
        raise HostDispatchRuntimeError(str(exc)) from exc


def _write_response(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise HostDispatchRuntimeError("Host dispatch response exceeds its size bound")
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(encoded, encoding="utf-8", newline="\n")
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _configured_binding(envelope: HostDispatchEnvelope) -> OperationProviderBinding:
    candidates = tuple(
        binding
        for binding in configured_provider_pool_from_environment()
        if binding.provider_id == envelope.provider_id
        and binding.binding_id == envelope.provider_binding_id
        and binding.model == envelope.model_id
    )
    if len(candidates) != 1:
        raise HostDispatchRuntimeError("Host dispatch identity is not one configured binding")
    return candidates[0]


def _dispatch_configured(envelope: HostDispatchEnvelope) -> ModelResponse:
    binding = _configured_binding(envelope)
    resolver = QualificationResolver()
    admitted = admit_resource_pool(
        (binding,),
        resolver=resolver,
        required_tier=envelope.intelligence_tier,
        required_capabilities=tuple(envelope.request.requested_capabilities),
        no_charge_required=True,
    )
    if len(admitted) != 1:
        raise HostDispatchRuntimeError("Host dispatch binding failed current admission")
    with compose_resource_pool(admitted, resolver=resolver, resource_id_prefix="devfarm-host") as runtime:
        dispatch = HostProviderDispatch(runtime.dispatcher, execution_boundary="host_process")
        try:
            response = dispatch.request(envelope.request)
        except ProviderError as exc:
            if dispatch.last_transport_category is not None:
                setattr(exc, "transport_failure_category", dispatch.last_transport_category.value)
            raise
    if response.provider != envelope.provider_id or response.model != envelope.model_id:
        raise HostDispatchRuntimeError("Host dispatch response identity mismatch")
    return response


def process_once(
    request_path: str | Path,
    response_path: str | Path,
    *,
    provider_factory: Callable[[HostDispatchEnvelope], ModelProvider] | None = None,
) -> dict[str, Any]:
    """Process exactly one request and write a bounded response projection."""

    try:
        envelope = _read_envelope(request_path)
        if provider_factory is None:
            response = _dispatch_configured(envelope)
        else:
            provider = provider_factory(envelope)
            response = HostProviderDispatch(provider, execution_boundary="host_process").request(envelope.request)
            if response.provider != envelope.provider_id or response.model != envelope.model_id:
                raise HostDispatchRuntimeError("Host dispatch response identity mismatch")
        result = {
            "status": "completed",
            "dispatch_id": envelope.dispatch_id,
            "provider_id": response.provider,
            "model_id": response.model,
            "response": response.to_dict(),
        }
    except ProviderError as exc:
        result = {
            "status": "failed",
            "category": exc.category,
            "retryable": exc.retryable,
            "failover_safe": exc.failover_safe,
            "reconciliation_required": exc.requires_reconciliation,
            "http_status": exc.http_status,
        }
        preserved = getattr(exc, "transport_failure_category", None)
        if isinstance(preserved, str):
            result["transport_failure_category"] = preserved
    except (HostDispatchRuntimeError, TypeError, ValueError) as exc:
        result = {
            "status": "rejected",
            "category": "host_configuration",
            "reconciliation_required": False,
        }
    _write_response(response_path, result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--response", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = process_once(args.request, args.response)
    except (OSError, HostDispatchRuntimeError) as exc:
        print(json.dumps({"status": "rejected", "category": "host_configuration"}, sort_keys=True))
        return 2
    print(json.dumps({key: value for key, value in result.items() if key not in {"response"}}, sort_keys=True))
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "HostDispatchRuntimeError",
    "create_host_process_executor",
    "main",
    "process_once",
]
