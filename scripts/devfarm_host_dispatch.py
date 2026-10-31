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
import math
import os
from pathlib import Path
import sys
from typing import Any, Callable
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.devfarm_resource_pool import admit_resource_pool, compose_resource_pool
from scripts.devfarm_provider_runtime import build_worker_provider
from scripts.devfarm_worker_admission import validate_worker_provider
from src.dev_agent.domain.protocol import ModelResponse
from src.dev_agent.operation import OperationProviderBinding, configured_provider_pool_from_environment
from src.dev_agent.providers.base import ModelProvider, ProviderError, transport_failure_metadata
from src.dev_agent.providers.host_dispatch import (
    HostDispatchEnvelope,
    HostProcessExecutor,
    HostProviderDispatch,
)
from src.dev_agent.resources.qualification import QualificationResolver
from src.dev_agent.resources.control import DispatchDenied, quality_tier_advisory_for
from src.dev_agent.resources.model_candidates import materialize_provider_bindings
from src.dev_agent.resources.model_evidence import ModelEvidenceCatalog
from src.dev_agent.resources.provider_policy import is_local_provider
from src.dev_agent.resources.provider_authority_constants import REAL_LOCAL_PROVIDER_IDS


MAX_REQUEST_BYTES = 512 * 1024
MAX_RESPONSE_BYTES = 512 * 1024


class HostDispatchRuntimeError(ValueError):
    """The Host one-shot dispatch request cannot be safely processed."""


def create_host_process_executor(
    request_dir: str | Path,
    *,
    timeout_seconds: float = 120.0,
    local_trial: bool = False,
) -> HostProcessExecutor:
    """Build the repository's static one-shot Host runtime command.

    Planner, Reviewer, Critic, and Worker callers share this exact command
    composition.  It is intentionally derived from this checked-in adapter,
    not from a caller-provided URL or arbitrary shell string.
    """

    if not isinstance(local_trial, bool):
        raise TypeError("local_trial must be a boolean")
    command = (sys.executable, str(Path(__file__).resolve()))
    if local_trial:
        command += ("--local-trial",)
    return HostProcessExecutor(
        command,
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
    catalog = ModelEvidenceCatalog.load_default().catalog
    candidates: list[OperationProviderBinding] = []
    for configured in configured_provider_pool_from_environment():
        if configured.provider_id != envelope.provider_id:
            continue
        try:
            materialized = materialize_provider_bindings(
                configured,
                catalog,
                expand_discovered_models=True,
            )
        except (TypeError, ValueError) as exc:
            raise HostDispatchRuntimeError("Host configured model catalog is invalid") from exc
        candidates.extend(
            binding
            for binding in materialized
            if (
                binding.binding_id == envelope.provider_binding_id
                or binding.qualification_binding_id == envelope.provider_binding_id
            )
            and binding.model == envelope.model_id
        )
    if len(candidates) != 1:
        raise HostDispatchRuntimeError("Host dispatch identity is not one configured binding")
    return candidates[0]


def _dispatch_local_trial(envelope: HostDispatchEnvelope, binding: OperationProviderBinding) -> ModelResponse:
    if binding.provider_id not in REAL_LOCAL_PROVIDER_IDS:
        raise HostDispatchRuntimeError("local-trial Host dispatch requires a real local Provider")
    if (
        binding.provider_id != envelope.provider_id
        or binding.model != envelope.model_id
        or binding.binding_id != envelope.provider_binding_id
    ):
        raise HostDispatchRuntimeError("local-trial Host dispatch requires the exact configured binding")
    if envelope.intelligence_tier != "L1":
        raise HostDispatchRuntimeError("local-trial Host dispatch is limited to L1")
    if not set(envelope.request.requested_capabilities).issubset({"text"}):
        raise HostDispatchRuntimeError("local-trial Host dispatch supports text capability only")
    if envelope.request.allowed_tools or envelope.request.tool_definitions or envelope.request.tool_results:
        raise HostDispatchRuntimeError("local-trial Host dispatch rejects tool fields")

    timeout_seconds = binding.timeout_seconds
    configured_timeout = os.environ.get("OLLAMA_TIMEOUT_SECONDS")
    if binding.provider_id == "ollama" and configured_timeout is not None:
        try:
            timeout_seconds = float(configured_timeout)
        except (TypeError, ValueError) as exc:
            raise HostDispatchRuntimeError("Host local Provider timeout is invalid") from exc
    if not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0:
        raise HostDispatchRuntimeError("Host local Provider timeout is invalid")

    provider = build_worker_provider(
        binding.provider_id,
        binding.model,
        float(timeout_seconds),
        binding.binding_id,
        allow_local=True,
    )
    provider_id, model_id, binding_id, tier, eligibility = validate_worker_provider(provider, allow_local=True)
    if (provider_id, model_id, binding_id, tier) != (
        envelope.provider_id,
        envelope.model_id,
        envelope.provider_binding_id,
        envelope.intelligence_tier,
    ):
        raise HostDispatchRuntimeError("local-trial Provider failed exact identity validation")
    if not (
        eligibility.eligible
        and eligibility.operator_activated
        and eligibility.capability_qualified
        and eligibility.billing_admitted
        and eligibility.billing_source == "local_runtime"
        and eligibility.reason == "local_trial"
    ):
        raise HostDispatchRuntimeError("local-trial Provider failed current Host admission")

    response = HostProviderDispatch(provider, execution_boundary="host_process").request(envelope.request)
    if response.provider != envelope.provider_id or response.model != envelope.model_id:
        raise HostDispatchRuntimeError("Host dispatch response identity mismatch")
    return response


def _dispatch_configured(envelope: HostDispatchEnvelope, *, local_trial: bool = False) -> ModelResponse:
    if not isinstance(local_trial, bool):
        raise TypeError("local_trial must be a boolean")
    binding = _configured_binding(envelope)
    if local_trial and is_local_provider(binding.provider_id):
        if binding.provider_id not in REAL_LOCAL_PROVIDER_IDS:
            raise HostDispatchRuntimeError("local-trial Host dispatch requires a real local Provider")
        return _dispatch_local_trial(envelope, binding)
    evidence = ModelEvidenceCatalog.load_default()
    resolver = QualificationResolver()
    quality_tier_advisory = quality_tier_advisory_for(envelope.request)
    admitted = admit_resource_pool(
        (binding,),
        resolver=resolver,
        required_tier=envelope.intelligence_tier,
        quality_tier_advisory=quality_tier_advisory,
        required_capabilities=tuple(envelope.request.requested_capabilities),
        no_charge_required=True,
        model_admission_resolver=evidence.resolver,
        model_catalog=evidence.catalog,
    )
    if not admitted:
        raise DispatchDenied("no_route", "Host dispatch has no currently admitted resource")
    if len(admitted) != 1:
        raise HostDispatchRuntimeError("Host dispatch admission returned an ambiguous resource set")
    with compose_resource_pool(
        admitted,
        resolver=resolver,
        model_admission_resolver=evidence.resolver,
        quality_tier_advisory=quality_tier_advisory,
        resource_id_prefix="devfarm-host",
    ) as runtime:
        dispatch = HostProviderDispatch(runtime.dispatcher, execution_boundary="host_process")
        try:
            response = dispatch.request(envelope.request)
        except ProviderError as exc:
            if dispatch.last_transport_diagnostics is not None:
                for key, value in dispatch.last_transport_diagnostics.items():
                    setattr(exc, key, value)
            raise
    if response.provider != envelope.provider_id or response.model != envelope.model_id:
        raise HostDispatchRuntimeError("Host dispatch response identity mismatch")
    return response


def process_once(
    request_path: str | Path,
    response_path: str | Path,
    *,
    provider_factory: Callable[[HostDispatchEnvelope], ModelProvider] | None = None,
    local_trial: bool = False,
) -> dict[str, Any]:
    """Process exactly one request and write a bounded response projection."""

    try:
        if not isinstance(local_trial, bool):
            raise TypeError("local_trial must be a boolean")
        envelope = _read_envelope(request_path)
        if provider_factory is None:
            response = _dispatch_configured(envelope, local_trial=local_trial)
        else:
            if local_trial:
                raise HostDispatchRuntimeError("local-trial requires the configured Host admission path")
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
        if exc.category == "transport" or isinstance(preserved, str):
            result.update(transport_failure_metadata(exc))
    except DispatchDenied as exc:
        # Resource admission and routing denials happen before the concrete
        # Provider is entered. Preserve that local, confirmed failure instead
        # of projecting it as an UNKNOWN external effect.
        result = {
            "status": "failed",
            "category": exc.category,
            "retryable": False,
            "failover_safe": False,
            "reconciliation_required": False,
            "http_status": None,
        }
    except (HostDispatchRuntimeError, TypeError, ValueError) as exc:
        result = {
            "status": "rejected",
            "category": "host_configuration",
            "reconciliation_required": False,
        }
    except Exception as exc:
        # The one-shot runtime must always leave a bounded response artifact.
        # Do not expose exception text or assume that an unexpected failure
        # happened before an external effect; callers must reconcile it and
        # must not blindly retry the dispatch.
        result = {
            "status": "failed",
            "category": "host_runtime_failure",
            "reconciliation_required": True,
            "exception_type": type(exc).__name__,
        }
    _write_response(response_path, result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--response", required=True, type=Path)
    parser.add_argument(
        "--local-trial",
        action="store_true",
        help="explicitly allow the existing bounded local-trial admission path",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = process_once(args.request, args.response, local_trial=args.local_trial)
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
