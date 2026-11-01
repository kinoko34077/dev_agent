"""Read-only Worker task feasibility checks.

The feasibility boundary answers whether a validated Worker manifest and the
exact Host-provided source context can fit the bounded input/output contract.
It does not choose a Provider, dispatch a request, mutate a manifest, or
silently shrink the task.  ``reframe_required`` is a signal for the existing
Commander/Refinement owner to split or narrow the task before a model call.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any

from scripts.devfarm_contracts import MAX_OUTBOUND_BYTES
from scripts.devfarm_errors import DevFarmError
from scripts.devfarm_worker_input import MAX_INPUT_FILE_BYTES
from scripts.devfarm_contracts import parse_host_test_command


class FeasibilityError(DevFarmError):
    """The feasibility input cannot be safely classified."""


_OUTPUT_MODES = frozenset({"full_result", "minimal_file_replacement", "localized_file_edit"})
_STATUS = frozenset({"fits", "reframe_required", "invalid_acceptance"})
_DEFAULT_OUTPUT_TOKENS = {
    "full_result": 4096,
    "minimal_file_replacement": 8192,
    "localized_file_edit": 4096,
}
_MAX_OUTPUT_TOKENS = 32_768
_CHARS_PER_TOKEN = 4
_OUTPUT_OVERHEAD_CHARS = 512
_MAX_EXPECTED_OUTPUT_CHARS = 32 * 1024


def _bounded_text(value: Any, name: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise FeasibilityError(f"{name} must be bounded non-empty text")
    return value.strip()


def _paths(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise FeasibilityError(f"{name} must be a path list")
    paths: list[str] = []
    for item in value:
        path = _bounded_text(item, f"{name}[]", maximum=512).replace("\\", "/")
        if path.startswith(("/", "./")) or ":" in path or ".." in path.split("/"):
            raise FeasibilityError(f"{name} contains an unsafe path")
        if path not in paths:
            paths.append(path)
    return tuple(paths)


def _bounded_strings(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise FeasibilityError(f"{name} must be a string list")
    if len(value) > 32:
        raise FeasibilityError(f"{name} contains too many items")
    return tuple(_bounded_text(item, f"{name}[]", maximum=2_000) for item in value)


@dataclass(frozen=True)
class WorkerFeasibility:
    """A deterministic, non-authorizing Worker input/output assessment."""

    task_id: str
    status: str
    output_mode: str
    output_capacity_tokens: int
    output_capacity_chars: int
    input_file_count: int
    input_bytes: int
    input_chars: int
    input_lines: int
    largest_input_bytes: int
    context_file_count: int
    context_bytes: int
    missing_context_files: tuple[str, ...]
    estimated_output_chars: int
    reasons: tuple[str, ...]
    recommended_action: str | None

    def __post_init__(self) -> None:
        if self.status not in _STATUS:
            raise FeasibilityError("feasibility status is unsupported")
        if self.output_mode not in _OUTPUT_MODES:
            raise FeasibilityError("feasibility output mode is unsupported")
        if self.output_capacity_tokens <= 0 or self.output_capacity_tokens > _MAX_OUTPUT_TOKENS:
            raise FeasibilityError("feasibility output capacity is out of bounds")
        for name in (
            "output_capacity_chars",
            "input_file_count",
            "input_bytes",
            "input_chars",
            "input_lines",
            "largest_input_bytes",
            "context_file_count",
            "context_bytes",
            "estimated_output_chars",
        ):
            if getattr(self, name) < 0:
                raise FeasibilityError(f"{name} must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "output_mode": self.output_mode,
            "output_capacity_tokens": self.output_capacity_tokens,
            "output_capacity_chars": self.output_capacity_chars,
            "input_file_count": self.input_file_count,
            "input_bytes": self.input_bytes,
            "input_chars": self.input_chars,
            "input_lines": self.input_lines,
            "largest_input_bytes": self.largest_input_bytes,
            "context_file_count": self.context_file_count,
            "context_bytes": self.context_bytes,
            "missing_context_files": list(self.missing_context_files),
            "estimated_output_chars": self.estimated_output_chars,
            "reasons": list(self.reasons),
            "recommended_action": self.recommended_action,
        }


def _output_capacity(manifest: Mapping[str, Any], output_mode: str, max_output_tokens: int | None) -> int:
    if max_output_tokens is None:
        configured = _DEFAULT_OUTPUT_TOKENS[output_mode]
    else:
        if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int):
            raise FeasibilityError("max_output_tokens must be an integer")
        configured = max_output_tokens
    if configured <= 0 or configured > _MAX_OUTPUT_TOKENS:
        raise FeasibilityError("max_output_tokens is out of bounds")
    declared = manifest.get("max_output_tokens")
    if declared is not None:
        if isinstance(declared, bool) or not isinstance(declared, int) or declared <= 0:
            raise FeasibilityError("manifest max_output_tokens is invalid")
        configured = min(configured, declared)
    return configured


def assess_worker_feasibility(
    manifest: Mapping[str, Any],
    file_contents: Mapping[str, bytes | str],
    *,
    max_output_tokens: int | None = None,
) -> WorkerFeasibility:
    """Classify a Worker task before Provider dispatch.

    ``file_contents`` is the exact, already-authorized Host context keyed by
    normalized outbound path.  New-file paths intentionally need no source
    entry.  Missing source context, malformed acceptance, or an unsafe output
    scope is never treated as a model failure and is reported as an invalid
    task contract instead.
    """

    if not isinstance(manifest, Mapping):
        raise FeasibilityError("manifest must be an object")
    if not isinstance(file_contents, Mapping):
        raise FeasibilityError("file_contents must be an object")
    task_id = _bounded_text(manifest.get("task_id"), "task_id")
    allowed_files = set(_paths(manifest.get("allowed_files"), "allowed_files"))
    outbound_files = _paths(manifest.get("outbound_files"), "outbound_files")
    new_files = _paths(manifest.get("new_files", []), "new_files")
    acceptance = _bounded_strings(manifest.get("acceptance", []), "acceptance")
    test_commands = _bounded_strings(manifest.get("test_commands", []), "test_commands")
    output_contract = manifest.get("output_contract")
    if not isinstance(output_contract, Mapping):
        raise FeasibilityError("output_contract must be an object")
    output_mode = output_contract.get("mode", "full_result")
    if output_mode not in _OUTPUT_MODES:
        raise FeasibilityError("output_contract.mode is unsupported")
    output_mode = str(output_mode)
    capacity_tokens = _output_capacity(manifest, output_mode, max_output_tokens)

    reasons: list[str] = []
    if not acceptance:
        reasons.append("acceptance_missing")
    if not test_commands:
        reasons.append("test_commands_missing")
    else:
        for command in test_commands:
            try:
                parse_host_test_command(command)
            except DevFarmError:
                reasons.append("test_command_invalid")
                break
    output_paths = set(outbound_files) | set(new_files)
    if not output_paths or not output_paths.issubset(allowed_files):
        reasons.append("output_scope_invalid")
    if set(new_files) & set(outbound_files):
        reasons.append("new_file_overlaps_input")

    total_bytes = 0
    total_chars = 0
    total_lines = 0
    largest_bytes = 0
    read_files = _paths(manifest.get("read_files", []), "read_files")
    context_bytes = 0
    missing_context: list[str] = []
    for relative in read_files:
        raw = file_contents.get(relative)
        if raw is None:
            missing_context.append(relative)
            continue
        if isinstance(raw, bytes):
            context_bytes += len(raw)
        elif isinstance(raw, str):
            context_bytes += len(raw.encode("utf-8"))
        else:
            raise FeasibilityError(f"file_contents[{relative}] must be bytes or text")
    missing_inputs: list[str] = []
    for relative in outbound_files:
        raw = file_contents.get(relative)
        if raw is None:
            missing_inputs.append(relative)
            continue
        if isinstance(raw, bytes):
            data = raw
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                reasons.append("input_not_utf8")
                continue
        elif isinstance(raw, str):
            text = raw
            data = raw.encode("utf-8")
        else:
            raise FeasibilityError(f"file_contents[{relative}] must be bytes or text")
        if b"\x00" in data:
            reasons.append("input_contains_nul")
        total_bytes += len(data)
        total_chars += len(text)
        total_lines += len(text.splitlines())
        largest_bytes = max(largest_bytes, len(data))
    reasons.extend(f"missing_input:{path}" for path in missing_inputs)
    reasons.extend(f"missing_context:{path}" for path in missing_context)
    if largest_bytes > MAX_INPUT_FILE_BYTES:
        reasons.append("input_file_too_large")
    if total_bytes > MAX_OUTBOUND_BYTES:
        reasons.append("input_scope_too_large")
    if context_bytes > MAX_OUTBOUND_BYTES:
        reasons.append("context_scope_too_large")

    expected_output_chars = output_contract.get("expected_output_chars")
    if expected_output_chars is not None:
        if (
            isinstance(expected_output_chars, bool)
            or not isinstance(expected_output_chars, int)
            or expected_output_chars <= 0
            or expected_output_chars > _MAX_EXPECTED_OUTPUT_CHARS
        ):
            raise FeasibilityError("output_contract.expected_output_chars is out of bounds")

    # This is intentionally a conservative envelope estimate.  It does not
    # claim a tokenization result; it only prevents a clearly impossible full
    # replacement from being sent to a bounded-output model.
    if output_mode == "localized_file_edit" and expected_output_chars is None:
        estimated_output_chars = _OUTPUT_OVERHEAD_CHARS
        reasons.append("localized_output_budget_missing")
    else:
        estimated_output_chars = (
            expected_output_chars
            if expected_output_chars is not None
            else max(_OUTPUT_OVERHEAD_CHARS, total_chars + _OUTPUT_OVERHEAD_CHARS)
        )
    output_capacity_chars = capacity_tokens * _CHARS_PER_TOKEN
    if estimated_output_chars > output_capacity_chars:
        reasons.append("output_envelope_too_large")

    unique_reasons = tuple(dict.fromkeys(reasons))
    if any(reason.startswith(("missing_input:", "missing_context:")) for reason in unique_reasons) or any(
        reason in {"acceptance_missing", "test_commands_missing", "test_command_invalid", "output_scope_invalid", "new_file_overlaps_input", "input_not_utf8", "input_contains_nul"}
        for reason in unique_reasons
    ):
        status = "invalid_acceptance"
        recommended_action = "repair_manifest_acceptance"
    elif unique_reasons:
        status = "reframe_required"
        recommended_action = (
            "declare_output_budget"
            if "localized_output_budget_missing" in unique_reasons
            else "split_input_scope"
        )
    else:
        status = "fits"
        recommended_action = None
    return WorkerFeasibility(
        task_id=task_id,
        status=status,
        output_mode=output_mode,
        output_capacity_tokens=capacity_tokens,
        output_capacity_chars=output_capacity_chars,
        input_file_count=len(outbound_files),
        input_bytes=total_bytes,
        input_chars=total_chars,
        input_lines=total_lines,
        largest_input_bytes=largest_bytes,
        context_file_count=len(read_files),
        context_bytes=context_bytes,
        missing_context_files=tuple(missing_context),
        estimated_output_chars=estimated_output_chars,
        reasons=unique_reasons,
        recommended_action=recommended_action,
    )


__all__ = ["FeasibilityError", "WorkerFeasibility", "assess_worker_feasibility"]
