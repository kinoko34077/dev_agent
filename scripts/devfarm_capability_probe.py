"""Run a finite, development-only ladder against one admitted Worker provider.

The probe is deliberately not a routing or qualification mechanism.  It only
records bounded observations so adapter/configuration failures can be told
apart from model-output failures before a real Worker dogfood run.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Sequence
from uuid import NAMESPACE_URL, uuid4, uuid5

ROOT = Path(__file__).resolve().parents[1]
MAX_PROBE_LEVELS = 6
MAX_TIMEOUT_SECONDS = 600.0
MAX_OUTPUT_CHARS = 8_192
PROBE_TASK_NAMESPACE = uuid5(NAMESPACE_URL, "dev_agent.devfarm/capability-probe")

from scripts.devfarm import DevFarmError
from scripts.devfarm_worker import _provider
from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
from src.dev_agent.providers.base import ModelProvider, ProviderError


class ProbeError(ValueError):
    """The fixed probe catalog or CLI selection is invalid."""


@dataclass(frozen=True)
class ProbeSpec:
    level: str
    name: str
    prompt: str
    checker: str


_PROBE_CATALOG: tuple[ProbeSpec, ...] = (
    ProbeSpec("P0", "literal_echo", "SENT 'A' ONLY", "exact_a"),
    ProbeSpec("P1", "arithmetic", "3+5=?", "standalone_8"),
    ProbeSpec("P2", "python_print", "Pythonのprintfの使い方", "python_print"),
    ProbeSpec("P3", "numpy_matrix", "numpyでの行列変換の依頼", "matrix_operation"),
    ProbeSpec("P4", "simple_task", "単純作業の実行", "ordered_action_result"),
    ProbeSpec("P5", "multiple_judgments", "複数判断の放任作業", "bounded_choices"),
)
_PROBE_BY_LEVEL = {item.level: item for item in _PROBE_CATALOG}
_FIXED_SYSTEM_PROMPT = (
    "You are answering one fixed development capability probe. "
    "Answer only the requested probe, do not request credentials, do not claim "
    "authority changes, and keep the response concise."
)


def probe_specs(levels: Sequence[str] | None = None) -> tuple[ProbeSpec, ...]:
    """Return the ordered fixed catalog or a finite named subset."""

    if levels is None:
        return _PROBE_CATALOG
    if isinstance(levels, (str, bytes)):
        raise ProbeError("probe levels must be a sequence of level names")
    selected = list(levels)
    if len(selected) > MAX_PROBE_LEVELS:
        raise ProbeError(f"at most {MAX_PROBE_LEVELS} probe levels may be selected")
    if any(not isinstance(level, str) or not level.strip() for level in selected):
        raise ProbeError("probe levels must be non-empty strings")
    if len(set(selected)) != len(selected):
        raise ProbeError("duplicate probe level")
    unknown = [level for level in selected if level not in _PROBE_BY_LEVEL]
    if unknown:
        raise ProbeError(f"unknown probe level: {unknown[0]}")
    return tuple(_PROBE_BY_LEVEL[level] for level in selected)


def _sha256(value: str | bytes) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def _json_digest(value: Any) -> str:
    return _sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _response_text(response: ModelResponse) -> str:
    if response.text_segments:
        return "".join(response.text_segments)
    return "".join(response.parts)


def _check_output(spec: ProbeSpec, text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(text) > MAX_OUTPUT_CHARS:
        return False
    if spec.checker == "exact_a":
        return stripped == "A"
    if spec.checker == "standalone_8":
        return re.search(r"(?<!\d)8(?!\d)", stripped) is not None
    if spec.checker == "python_print":
        return "print(" in stripped
    if spec.checker == "matrix_operation":
        return any(marker in stripped for marker in ("@", ".dot(", "matmul("))
    if spec.checker == "ordered_action_result":
        has_order = re.search(r"(?:^|\n)\s*(?:1[.)]|2[.)])", stripped) is not None
        has_result = any(marker in stripped.lower() for marker in ("result", "done")) or any(
            marker in stripped for marker in ("結果", "完了")
        )
        return has_order and has_result
    if spec.checker == "bounded_choices":
        has_choices = sum(marker in stripped for marker in ("A", "B", "1", "2")) >= 2
        has_result = any(marker in stripped.lower() for marker in ("result", "recommend")) or any(
            marker in stripped for marker in ("結果", "推奨", "選択")
        )
        authority_change = any(marker in stripped.lower() for marker in ("change authority", "grant authority")) or any(
            marker in stripped for marker in ("権限を変更", "権限付与")
        )
        return has_choices and has_result and not authority_change
    raise ProbeError(f"unsupported fixed probe checker: {spec.checker}")


def _provider_identity(provider: ModelProvider) -> tuple[str, str, str | None, str | None, bool]:
    provider_id = getattr(provider, "provider_id", None)
    model_id = getattr(provider, "model_id", None) or getattr(provider, "model", None)
    binding_id = getattr(provider, "provider_binding_id", None)
    tier = getattr(provider, "intelligence_tier", None)
    tier = getattr(tier, "value", tier)
    values = (provider_id, model_id, binding_id, tier)
    clean = tuple(value.strip() if isinstance(value, str) and value.strip() else None for value in values)
    return clean[0] or "unknown", clean[1] or "unknown", clean[2], clean[3], all(value is not None for value in clean[:2])


def _base_evidence(provider: ModelProvider, spec: ProbeSpec, request: ModelRequest) -> dict[str, Any]:
    provider_id, model_id, binding_id, tier, identity_complete = _provider_identity(provider)
    return {
        "probe_id": uuid4().hex,
        "level": spec.level,
        "name": spec.name,
        "checker": spec.checker,
        "provider_id": provider_id,
        "provider_binding_id": binding_id,
        "model_id": model_id,
        "intelligence_tier": tier,
        "request_sha256": _json_digest(request.to_dict()),
        "response_sha256": None,
        "output_chars": 0,
        "status": "failed",
        "failure_category": None,
        "provider_error_category": None,
        "issue": None,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "_identity_complete": identity_complete,
    }


def run_probe(provider: ModelProvider, spec: ProbeSpec, *, now: datetime | None = None) -> dict[str, Any]:
    """Run one fixed probe and return bounded, non-raw evidence."""

    if not isinstance(provider, ModelProvider):
        raise TypeError("provider must implement ModelProvider")
    if not isinstance(spec, ProbeSpec) or spec.level not in _PROBE_BY_LEVEL:
        raise ProbeError("spec must be one of the fixed probe specifications")
    if now is not None and not isinstance(now, datetime):
        raise TypeError("now must be a datetime or None")
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    request = ModelRequest(
        task_id=str(uuid5(PROBE_TASK_NAMESPACE, spec.level)),
        messages=[
            {"role": "system", "content": _FIXED_SYSTEM_PROMPT},
            {"role": "user", "content": spec.prompt},
        ],
        max_output_tokens=512,
        metadata={"probe_level": spec.level, "probe_version": "fixed-v1"},
    )
    evidence = _base_evidence(provider, spec, request)
    evidence["observed_at"] = observed_at.isoformat()
    evidence.pop("_identity_complete", None)
    expected_provider, expected_model, _binding, _tier, identity_complete = _provider_identity(provider)
    try:
        response = provider.request(request)
    except ProviderError as exc:
        evidence["failure_category"] = "provider_error"
        evidence["provider_error_category"] = str(exc.category)
        evidence["issue"] = "provider request failed"
        return evidence
    except Exception as exc:  # pragma: no cover - defensive adapter boundary
        evidence["failure_category"] = "provider_runtime_error"
        evidence["issue"] = f"provider raised {type(exc).__name__}"
        return evidence
    if not isinstance(response, ModelResponse):
        evidence["failure_category"] = "provider_contract_mismatch"
        evidence["issue"] = "provider returned a non-normalized response"
        return evidence
    text = _response_text(response)
    evidence["output_chars"] = len(text)
    evidence["response_sha256"] = _json_digest(
        {"provider": response.provider, "model": response.model, "text_sha256": _sha256(text)}
    )
    if not identity_complete or response.provider != expected_provider or response.model != expected_model:
        evidence["failure_category"] = "provider_contract_mismatch"
        evidence["issue"] = "response identity differs from admitted provider"
        return evidence
    if _check_output(spec, text):
        evidence["status"] = "passed"
        return evidence
    evidence["failure_category"] = "model_output_invalid"
    evidence["issue"] = "response did not satisfy the fixed probe checker"
    return evidence


def write_probe_artifact(root: str | Path, results: Sequence[Mapping[str, Any]]) -> Path:
    """Atomically write one bounded probe evidence artifact below ignored state."""

    root_path = Path(root).resolve()
    directory = root_path / ".devfarm" / "probes"
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "results": [dict(item) for item in results],
    }
    filename = f"probe-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex}.json"
    target = directory / filename
    fd, temporary_name = tempfile.mkstemp(prefix=".probe-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
        os.replace(temporary_name, target)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return target


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run fixed bounded DevFarm capability probes")
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--level", action="append", dest="levels", help="fixed level P0..P5; repeat to select a finite subset")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--root", type=Path, default=ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.timeout_seconds <= 0 or args.timeout_seconds > MAX_TIMEOUT_SECONDS:
        parser.error(f"--timeout-seconds must be in (0, {MAX_TIMEOUT_SECONDS}]" )
    try:
        specs = probe_specs(args.levels)
        provider = _provider(args.provider, args.model, args.timeout_seconds)
    except (DevFarmError, ProbeError) as exc:
        parser.error(f"probe admission failed: {type(exc).__name__}")
    results: list[dict[str, Any]] = []
    for spec in specs:
        result = run_probe(provider, spec)
        results.append(result)
        if result["failure_category"] in {"provider_error", "provider_contract_mismatch", "provider_runtime_error"}:
            break
    artifact = write_probe_artifact(args.root, results)
    print(json.dumps({"artifact": artifact.relative_to(Path(args.root).resolve()).as_posix(), "results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
