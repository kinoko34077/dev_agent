"""Model-neutral adapter for bounded, proposal-only planning.

This module converts one existing ``ModelProvider`` response into the typed
``RootPlanningProposal`` already validated by the host Operation boundary. It
does not create Tasks, reserve budget, approve execution, or write durable
state. Provider selection and all authority checks remain outside this
adapter.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import math
import re
from typing import Any

from ..domain.protocol import ModelRequest, ModelResponse
from ..providers.base import ModelProvider
from .planner import PlanningValidationError, RootPlanningProposal


class PlanningAdapterError(ValueError):
    """The provider response cannot be treated as a typed planning proposal."""


PLANNING_PROPOSAL_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["parent_task_id", "rationale", "children"],
    "properties": {
        "parent_task_id": {"type": "string"},
        "rationale": {"type": "string"},
        "planning_cycle": {"type": "integer", "minimum": 1},
        "proposal_id": {"type": "string"},
        "children": {
            "type": "array",
            "minItems": 1,
            "maxItems": 32,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["child_key", "objective", "task_type"],
                "properties": {
                    "child_key": {"type": "string"},
                    "objective": {"type": "string"},
                    "task_type": {"type": "string"},
                    "risk": {"type": "string"},
                    "sensitivity": {"type": ["string", "null"]},
                    "required_capabilities": {"type": "array", "items": {"type": "string"}},
                    "dependencies": {"type": "array", "items": {"type": "string"}},
                    "dependency_types": {"type": "object", "additionalProperties": {"type": "string"}},
                    "suggested_owner": {"type": "string"},
                },
            },
        },
    },
}

_SENSITIVITIES = {"public", "normal", "internal", "sensitive"}
_INTELLIGENCE_TIERS = {"L0", "L1", "L2", "L3"}
_FENCED_JSON = re.compile(r"^```(?:json)?\s*\r?\n(?P<body>.*?)\r?\n```$", re.IGNORECASE | re.DOTALL)


class ModelPlanningAdapter:
    """Request and decode one finite proposal from an injected provider.

    The provider can be a normal ``ProviderDispatcher`` selected by existing
    routing or a focused test double.  The adapter deliberately does not
    infer a tier from a model name and does not claim that a provider is free.
    """

    _MAX_OBJECTIVE_CHARS = 12_000
    _MAX_REFERENCE_BYTES = 24_000
    _MAX_RESPONSE_CHARS = 64_000
    _MAX_CHILDREN = 32

    def __init__(
        self,
        provider: ModelProvider,
        *,
        max_output_tokens: int = 2_048,
        cost_ceiling: float = 0.0,
        allow_unknown_quota: bool = False,
    ) -> None:
        if not callable(getattr(provider, "request", None)):
            raise TypeError("provider must expose request(ModelRequest)")
        if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or not 0 < max_output_tokens <= 8_192:
            raise ValueError("max_output_tokens must be between 1 and 8192")
        if isinstance(cost_ceiling, bool) or not isinstance(cost_ceiling, (int, float)) or not math.isfinite(float(cost_ceiling)) or cost_ceiling < 0:
            raise ValueError("cost_ceiling must be a finite non-negative number")
        if not isinstance(allow_unknown_quota, bool):
            raise TypeError("allow_unknown_quota must be a boolean")
        self.provider = provider
        self.max_output_tokens = max_output_tokens
        self.cost_ceiling = float(cost_ceiling)
        self.allow_unknown_quota = allow_unknown_quota

    def propose(
        self,
        *,
        parent_task_id: str,
        objective: str,
        sensitivity: str = "normal",
        required_intelligence_tier: str | None = None,
        context_references: Mapping[str, Any] | None = None,
    ) -> RootPlanningProposal:
        """Return one typed proposal; host validation remains a separate step."""

        if not isinstance(parent_task_id, str) or not parent_task_id.strip():
            raise PlanningAdapterError("parent_task_id must be a non-empty string")
        if not isinstance(objective, str) or not objective.strip():
            raise PlanningAdapterError("objective must be a non-empty string")
        objective = objective.strip()
        if len(objective) > self._MAX_OBJECTIVE_CHARS:
            raise PlanningAdapterError("objective exceeds the planning input limit")
        if not isinstance(sensitivity, str) or sensitivity.strip().lower() not in _SENSITIVITIES:
            raise PlanningAdapterError("sensitivity is invalid")
        if required_intelligence_tier is not None:
            if (
                not isinstance(required_intelligence_tier, str)
                or required_intelligence_tier.strip() not in _INTELLIGENCE_TIERS
            ):
                raise PlanningAdapterError("required_intelligence_tier is invalid")
            required_intelligence_tier = required_intelligence_tier.strip()
        references = self._references(context_references)
        content = self._prompt(
            parent_task_id.strip(),
            objective,
            sensitivity.strip().lower(),
            references,
            required_intelligence_tier,
        )
        metadata = {
            "planning_mode": "proposal_only",
            "authority": "host_validation_required",
            "planner_response_encoding": "strict_json_text",
        }
        if required_intelligence_tier is not None:
            metadata.update(
                {
                    "intelligence_routing": "bounded",
                    "allowed_intelligence_tiers": [required_intelligence_tier],
                }
            )
        if self.allow_unknown_quota:
            # This only permits the existing one-shot UNKNOWN quota admission
            # path.  ResourceControlPlane still requires the exact trusted
            # no-charge catalog entry; this flag never invents quota headroom.
            metadata["allow_unknown_quota"] = True
        try:
            request = ModelRequest(
                task_id=parent_task_id.strip(),
                messages=[{"role": "user", "content": content}],
                # The current qualified L2 Gemini binding is certified for
                # text plus strict JSON decoding, not for a separate
                # structured-output routing capability.  Keep the wire
                # requirement honest; response_schema remains a provider
                # hint and the decoder is the authority at this boundary.
                requested_capabilities=["text"],
                response_schema=PLANNING_PROPOSAL_RESPONSE_SCHEMA,
                max_output_tokens=self.max_output_tokens,
                sensitivity=sensitivity.strip().lower(),
                cost_ceiling=self.cost_ceiling,
                metadata=metadata,
            )
        except (TypeError, ValueError) as exc:
            raise PlanningAdapterError(f"invalid planning request: {exc}") from exc
        response = self.provider.request(request)
        payload = self._response_payload(response)
        try:
            proposal = RootPlanningProposal.from_dict(payload)
        except PlanningValidationError as exc:
            raise PlanningAdapterError(str(exc)) from exc
        if proposal.parent_task_id != parent_task_id.strip():
            raise PlanningAdapterError("proposal parent_task_id does not match the requested parent")
        if len(proposal.children) > self._MAX_CHILDREN:
            raise PlanningAdapterError("planning proposal exceeds the child limit")
        return proposal

    @classmethod
    def _references(cls, references: Mapping[str, Any] | None) -> dict[str, Any]:
        if references is None:
            return {}
        if not isinstance(references, Mapping):
            raise PlanningAdapterError("context_references must be an object")
        try:
            serialized = json.dumps(dict(references), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise PlanningAdapterError("context_references must be JSON serializable") from exc
        if len(serialized.encode("utf-8")) > cls._MAX_REFERENCE_BYTES:
            raise PlanningAdapterError("context_references exceed the planning input limit")
        return dict(references)

    @staticmethod
    def _prompt(
        parent_task_id: str,
        objective: str,
        sensitivity: str,
        references: Mapping[str, Any],
        required_intelligence_tier: str | None,
    ) -> str:
        reference_text = json.dumps(dict(references), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        tier_text = required_intelligence_tier or "host-selected"
        return (
            "Generate exactly one raw JSON object matching the supplied planning response schema. "
            "Do not emit Markdown fences, explanatory prose, comments, or trailing text.\n"
            "This is a proposal only: do not claim authority, budget, approval, privacy relaxation, "
            "Gate changes, or direct Task creation. The host will validate the proposal.\n"
            f"parent_task_id: {parent_task_id}\n"
            f"sensitivity: {sensitivity}\n"
            f"required_intelligence_tier: {tier_text}\n"
            f"objective:\n{objective}\n"
            f"reference_context:\n{reference_text}"
        )

    @classmethod
    def _response_payload(cls, response: ModelResponse) -> Mapping[str, Any]:
        if not isinstance(response, ModelResponse):
            raise PlanningAdapterError("planner provider returned an invalid response type")
        if response.structured_output is not None:
            return response.structured_output
        text = "".join(response.text_segments or response.parts)
        if not isinstance(text, str) or not text.strip():
            raise PlanningAdapterError("planner response has no structured JSON output")
        if len(text) > cls._MAX_RESPONSE_CHARS:
            raise PlanningAdapterError("planner response exceeds the response limit")
        candidate = text.strip()
        fenced = _FENCED_JSON.fullmatch(candidate)
        if fenced is not None:
            candidate = fenced.group("body").strip()
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise PlanningAdapterError("planner response is not valid JSON") from exc
        if not isinstance(payload, Mapping):
            raise PlanningAdapterError("planner response JSON must be an object")
        return payload


__all__ = ["ModelPlanningAdapter", "PLANNING_PROPOSAL_RESPONSE_SCHEMA", "PlanningAdapterError"]
