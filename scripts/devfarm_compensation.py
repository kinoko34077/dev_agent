"""Host-owned, failure-specific compensation selection for Worker rework.

The selector is intentionally pure.  It does not dispatch a Provider, mutate a
manifest, or change authority.  The existing Commander/Refinement boundaries
persist and apply its bounded decision only on a fresh rework attempt.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from scripts.devfarm_errors import DevFarmError


_OUTPUT_MODES = frozenset({"full_result", "minimal_file_replacement", "localized_file_edit"})
_FAILURE_CLASSES = frozenset(
    {
        "FORMAT_PATCH",
        "SEMANTIC_TEST",
        "CAPABILITY_REASONING",
        "PROVIDER_TRANSPORT",
        "SECURITY_EGRESS_AUTHORITY",
        "UNKNOWN_EXTERNAL_EFFECT",
    }
)
_BOUNDARY_FAILURES = frozenset(
    {
        "PROVIDER_TRANSPORT",
        "SECURITY_EGRESS_AUTHORITY",
        "UNKNOWN_EXTERNAL_EFFECT",
    }
)
_DEFAULT_LOCALIZED_OUTPUT_CHARS = 4_096


@dataclass(frozen=True)
class WorkerCompensation:
    """One bounded correction/reframing decision owned by the Host."""

    action: str
    reason_code: str
    output_mode: str | None
    expected_output_chars: int | None = None
    context_hints: tuple[str, ...] = ()
    reframe_required: bool = False
    authority: str = "host_policy"

    def __post_init__(self) -> None:
        if self.expected_output_chars is not None and (
            isinstance(self.expected_output_chars, bool)
            or not isinstance(self.expected_output_chars, int)
            or self.expected_output_chars <= 0
            or self.expected_output_chars > 32 * 1024
        ):
            raise DevFarmError("expected_output_chars is out of bounds")

    @property
    def prompt_contract(self) -> str:
        """Return the bounded instruction contract for the selected action."""

        return {
            "switch_output_mode": "host-localized-edit",
            "repair_same_task": "bounded-repair-delta",
            "add_minimal_context": "bounded-category-context",
            "reframe_task": "upper-reframe",
            "stop_model_repair": "no-model-replay",
        }.get(self.action, "no-model-replay")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason_code": self.reason_code,
            "output_mode": self.output_mode,
            "expected_output_chars": self.expected_output_chars,
            "context_hints": list(self.context_hints),
            "reframe_required": self.reframe_required,
            "authority": self.authority,
            "prompt_contract": self.prompt_contract,
        }


def _normalized(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DevFarmError(f"{name} must be non-empty text")
    return value.strip().upper().replace("-", "_").replace(" ", "_")


def _current_mode(manifest: Mapping[str, Any], explicit: str | None) -> str:
    candidate = explicit
    if candidate is None:
        contract = manifest.get("output_contract")
        if isinstance(contract, Mapping):
            candidate = contract.get("mode")
    if candidate is None:
        candidate = "full_result"
    if not isinstance(candidate, str) or candidate not in _OUTPUT_MODES:
        raise DevFarmError("current_output_mode is unsupported")
    return candidate


def _existing_outbound(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    outbound = manifest.get("outbound_files", ())
    new_files = manifest.get("new_files", ())
    if isinstance(outbound, (str, bytes)) or not isinstance(outbound, (list, tuple)):
        raise DevFarmError("manifest outbound_files must be a sequence")
    if isinstance(new_files, (str, bytes)) or not isinstance(new_files, (list, tuple)):
        raise DevFarmError("manifest new_files must be a sequence")
    new_set = {item for item in new_files if isinstance(item, str)}
    return tuple(item for item in outbound if isinstance(item, str) and item not in new_set)


def select_worker_compensation(
    manifest: Mapping[str, Any],
    *,
    failure_class: str,
    failure_code: str,
    stage: str,
    current_output_mode: str | None = None,
) -> WorkerCompensation:
    """Select one failure-specific bounded next action.

    Only fixed, allowlisted diagnostic categories are accepted.  No raw error
    text is returned or embedded in the decision, and boundary failures never
    become model repair work.
    """

    if not isinstance(manifest, Mapping):
        raise DevFarmError("manifest must be an object")
    failure = _normalized(failure_class, "failure_class")
    code = _normalized(failure_code, "failure_code")
    _normalized(stage, "stage")
    if failure not in _FAILURE_CLASSES:
        raise DevFarmError(f"failure_class is unsupported: {failure}")
    mode = _current_mode(manifest, current_output_mode)
    existing = _existing_outbound(manifest)

    if failure in _BOUNDARY_FAILURES:
        return WorkerCompensation(
            action="stop_model_repair",
            reason_code="boundary_failure_requires_existing_authority",
            output_mode=None,
        )

    if failure == "CAPABILITY_REASONING":
        return WorkerCompensation(
            action="reframe_task",
            reason_code="capability_or_context_exceeded",
            output_mode=None,
            context_hints=("split_independent_region", "preserve_acceptance_test"),
            reframe_required=True,
        )

    if failure == "FORMAT_PATCH":
        if existing and mode != "localized_file_edit":
            return WorkerCompensation(
                action="switch_output_mode",
                reason_code="format_existing_source_contract",
                output_mode="localized_file_edit",
                expected_output_chars=_DEFAULT_LOCALIZED_OUTPUT_CHARS,
                context_hints=("exact_line_anchor", "expected_lines", "host_generated_diff"),
            )
        return WorkerCompensation(
            action="repair_same_task",
            reason_code="format_new_file_contract" if not existing else "format_localized_contract",
            output_mode=mode,
            context_hints=("schema_only", "one_json_object", "preserve_scope"),
        )

    # Syntax/import failures have a bounded, independently checkable local
    # target.  Other semantic failures receive only category-level context;
    # the Host never forwards raw traceback or secret-bearing output.
    if code in {"WORKER_HOST_VERIFICATION_SYNTAX", "WORKER_OUTPUT_LINE_SHAPE"} and existing:
        return WorkerCompensation(
            action="switch_output_mode",
            reason_code="syntax_or_line_shape_existing_source",
            output_mode="localized_file_edit",
            expected_output_chars=_DEFAULT_LOCALIZED_OUTPUT_CHARS,
            context_hints=("host_failure_category", "exact_line_anchor", "independent_test_required"),
        )
    return WorkerCompensation(
        action="add_minimal_context",
        reason_code="semantic_failure_with_bounded_host_category",
        output_mode=mode,
        context_hints=("host_failure_category", "independent_test_required", "preserve_public_symbols"),
    )


__all__ = ["WorkerCompensation", "select_worker_compensation"]
