"""Model-neutral adapter for bounded, proposal-only planning.

This module converts one existing ``ModelProvider`` response into the typed
``RootPlanningProposal`` already validated by the host Operation boundary. It
does not create Tasks, reserve budget, approve execution, or write durable
state. Provider selection and all authority checks remain outside this
adapter.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import math
from typing import Any
from uuid import UUID

from ..domain.capabilities import CANONICAL_EXECUTION_CAPABILITIES
from ..domain.protocol import ModelRequest, ModelResponse
from ..providers.base import ModelProvider, ProviderError
from ..security.egress import EgressValidationError, attach_model_request_egress
from .capabilities import TASK_COMPETENCIES, TASK_POLICY_TRAITS
from .planner import PlanningValidationError, RootPlanningProposal
from .structured_response import StructuredResponseError, decode_json_object


class PlanningAdapterError(ValueError):
    """The provider response cannot be treated as a typed planning proposal."""


class PlanningResponseError(PlanningAdapterError):
    """The provider returned a response that failed the planning contract."""

    _RESPONSE_CONTRACTS = frozenset({"invalid_json", "invalid_proposal"})

    def __init__(
        self,
        message: str,
        *,
        request_id: str | None = None,
        provider_response_observed: bool = False,
        response_contract: str | None = None,
    ) -> None:
        super().__init__(message)
        if response_contract is not None and response_contract not in self._RESPONSE_CONTRACTS:
            raise ValueError("response_contract must be invalid_json or invalid_proposal")
        self.request_id = request_id
        self.provider_response_observed = provider_response_observed
        self.response_contract = response_contract


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
                    "required_capabilities": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": sorted(CANONICAL_EXECUTION_CAPABILITIES | TASK_COMPETENCIES | TASK_POLICY_TRAITS),
                        },
                    },
                    "dependencies": {"type": "array", "items": {"type": "string"}},
                    "dependency_types": {"type": "object", "additionalProperties": {"type": "string"}},
                    "suggested_owner": {"type": "string"},
                },
            },
        },
    },
}


PLANNING_CRITIC_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["corrected_proposal"],
    "properties": {
        "corrected_proposal": PLANNING_PROPOSAL_RESPONSE_SCHEMA,
    },
}

_SENSITIVITIES = {"public", "normal", "internal", "sensitive"}
_INTELLIGENCE_TIERS = {"L0", "L1", "L2", "L3"}
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
            "task_fit": "planning",
            "prefer_diversity": True,
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
        try:
            request, _egress_manifest = attach_model_request_egress(request)
        except EgressValidationError as exc:
            raise PlanningAdapterError(f"model request egress rejected: {exc}") from exc
        try:
            response = self.provider.request(request)
        except ProviderError as exc:
            # Keep the exact request identity available to the outer Host
            # projection without copying provider message/body details.
            exc.request_id = request.request_id
            raise
        try:
            payload = self._response_payload(response)
            proposal = RootPlanningProposal.from_dict(payload)
            if proposal.parent_task_id != parent_task_id.strip():
                raise PlanningResponseError(
                    "proposal parent_task_id does not match the requested parent",
                    response_contract="invalid_proposal",
                )
            if len(proposal.children) > self._MAX_CHILDREN:
                raise PlanningResponseError(
                    "planning proposal exceeds the child limit",
                    response_contract="invalid_proposal",
                )
        except PlanningResponseError as exc:
            # Keep correlation to the exact request that produced the
            # response, while leaving the response body and error text out of
            # the bounded failure projection.
            exc.request_id = request.request_id
            exc.provider_response_observed = True
            raise
        except PlanningValidationError as exc:
            raise PlanningResponseError(
                str(exc),
                request_id=request.request_id,
                provider_response_observed=True,
                response_contract="invalid_proposal",
            ) from exc
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
            "The top-level object must contain `parent_task_id`, `rationale`, and a `children` array. "
            "Every child field, including `child_key`, `objective`, `task_type`, `risk`, `sensitivity`, "
            "`required_capabilities`, `dependencies`, `dependency_types`, and `suggested_owner`, "
            "must be nested inside one object in `children`; do not place child fields at the top level. "
            "If a child has no dependencies, use `dependencies`: [] and `dependency_types`: {}; never use null for either field. "
            "Use `suggested_owner` exactly as `worker` or `codex`, never a descriptive phrase. "
            "Use only these exact required_capabilities values: architecture, coding, review, extraction, classification, translation, documentation, multilingual, security, protected, recovery, security_sensitive, private, sensitive, text, tool_call, structured_output, json, or long_context; use `coding` for a code/test task or [] when none is needed. "
            "Use this shape: {\"parent_task_id\":\"...\",\"rationale\":\"...\",\"children\":[{\"child_key\":\"...\",\"objective\":\"...\",\"task_type\":\"worker\"}]}.\n"
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
        try:
            return decode_json_object(response, role="planner", max_chars=cls._MAX_RESPONSE_CHARS)
        except StructuredResponseError as exc:
            raise PlanningResponseError(
                str(exc),
                response_contract="invalid_json",
            ) from exc


class PlanningCriticAdapterError(PlanningAdapterError):
    """The bounded independent planning correction cannot be accepted."""


class ModelPlanningCriticAdapter:
    """Request one independent, proposal-only correction for a bad plan response.

    This adapter is deliberately narrower than ``ModelPlanningAdapter``.  It
    can only be called for a response-contract failure that already observed a
    provider response.  It emits one fresh request and returns a typed
    proposal; the existing Host validator remains the authority that can
    accept it.  Provider selection, quota admission, and external execution
    remain outside this class.
    """

    _MAX_RESPONSE_CHARS = 64_000
    _MAX_OBJECTIVE_CHARS = ModelPlanningAdapter._MAX_OBJECTIVE_CHARS
    _MAX_REQUEST_ID_CHARS = 128

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
        if (
            isinstance(cost_ceiling, bool)
            or not isinstance(cost_ceiling, (int, float))
            or not math.isfinite(float(cost_ceiling))
            or cost_ceiling < 0
        ):
            raise ValueError("cost_ceiling must be a finite non-negative number")
        if not isinstance(allow_unknown_quota, bool):
            raise TypeError("allow_unknown_quota must be a boolean")
        self.provider = provider
        self.max_output_tokens = max_output_tokens
        self.cost_ceiling = float(cost_ceiling)
        self.allow_unknown_quota = allow_unknown_quota
        # The Host may project this bounded correlation for evidence.  It is
        # reset for every correction so a successful Planner attempt can never
        # inherit a stale Critic request identity.
        self.last_request_id: str | None = None

    def correct(
        self,
        *,
        parent_task_id: str,
        objective: str,
        planner_failure: PlanningResponseError,
        sensitivity: str = "normal",
        context_references: Mapping[str, Any] | None = None,
    ) -> RootPlanningProposal:
        """Return one corrected proposal after a model-output contract failure."""

        self.last_request_id = None
        parent_task_id = self._parent_task_id(parent_task_id)
        objective = self._objective(objective)
        sensitivity = self._sensitivity(sensitivity)
        if not isinstance(planner_failure, PlanningResponseError):
            raise PlanningCriticAdapterError(
                "planning critic requires a response-contract failure"
            )
        if planner_failure.provider_response_observed is not True:
            raise PlanningCriticAdapterError(
                "planning critic requires an observed provider response"
            )
        response_contract = planner_failure.response_contract
        if response_contract not in PlanningResponseError._RESPONSE_CONTRACTS:
            raise PlanningCriticAdapterError(
                "planning critic requires an invalid_json or invalid_proposal response-contract failure"
            )
        references = ModelPlanningAdapter._references(context_references)
        source_request_id = self._request_id(planner_failure.request_id)
        content = self._prompt(
            parent_task_id=parent_task_id,
            objective=objective,
            sensitivity=sensitivity,
            response_contract=response_contract,
            source_request_id=source_request_id,
            references=references,
        )
        metadata: dict[str, Any] = {
            "planning_mode": "proposal_only",
            "planning_refinement": "independent_critic",
            "authority": "host_validation_required",
            "proposal_only": True,
            "integration_authority": "host_and_codex",
            "model_selection": "host_selected",
            "task_fit": "planning",
            "intelligence_routing": "bounded",
            "allowed_intelligence_tiers": ["L1"],
            "source_response_contract": response_contract,
        }
        if source_request_id is not None:
            metadata["critic_of_request_id"] = source_request_id
        if self.allow_unknown_quota:
            # Reuse the existing bounded UNKNOWN quota admission.  This flag
            # never creates quota headroom or changes billing/privacy policy.
            metadata["allow_unknown_quota"] = True
        try:
            request = ModelRequest(
                task_id=parent_task_id,
                messages=[{"role": "user", "content": content}],
                requested_capabilities=["text"],
                response_schema=PLANNING_CRITIC_RESPONSE_SCHEMA,
                max_output_tokens=self.max_output_tokens,
                sensitivity=sensitivity,
                cost_ceiling=self.cost_ceiling,
                metadata=metadata,
            )
        except (TypeError, ValueError) as exc:
            raise PlanningCriticAdapterError(f"invalid planning critic request: {exc}") from exc
        try:
            request, _egress_manifest = attach_model_request_egress(request)
        except EgressValidationError as exc:
            raise PlanningCriticAdapterError(f"model request egress rejected: {exc}") from exc
        self.last_request_id = request.request_id
        try:
            response = self.provider.request(request)
        except ProviderError as exc:
            # Keep the independent Critic failure correlated to its own fresh
            # request without exposing provider response or exception text.
            exc.request_id = request.request_id
            raise
        try:
            payload = decode_json_object(
                response,
                role="planning critic",
                max_chars=self._MAX_RESPONSE_CHARS,
            )
            unknown = set(payload) - {"corrected_proposal"}
            if unknown:
                raise PlanningCriticAdapterError(
                    f"unknown planning critic response field: {sorted(unknown)[0]}"
                )
            proposal = RootPlanningProposal.from_dict(payload.get("corrected_proposal"))
        except StructuredResponseError as exc:
            error = PlanningCriticAdapterError(str(exc))
            error.request_id = request.request_id
            raise error from exc
        except PlanningCriticAdapterError as exc:
            exc.request_id = request.request_id
            raise
        except PlanningValidationError as exc:
            error = PlanningCriticAdapterError(
                f"planning critic corrected proposal is invalid: {exc}"
            )
            error.request_id = request.request_id
            raise error from exc
        if proposal.parent_task_id != parent_task_id:
            error = PlanningCriticAdapterError(
                "planning critic corrected proposal parent_task_id does not match the requested parent"
            )
            error.request_id = request.request_id
            raise error
        return proposal

    @classmethod
    def _parent_task_id(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise PlanningCriticAdapterError("parent_task_id must be a non-empty string")
        return value.strip()

    @classmethod
    def _objective(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise PlanningCriticAdapterError("objective must be a non-empty string")
        normalized = value.strip()
        if len(normalized) > cls._MAX_OBJECTIVE_CHARS:
            raise PlanningCriticAdapterError("objective exceeds the planning input limit")
        return normalized

    @staticmethod
    def _sensitivity(value: Any) -> str:
        if not isinstance(value, str) or value.strip().lower() not in _SENSITIVITIES:
            raise PlanningCriticAdapterError("sensitivity is invalid")
        return value.strip().lower()

    @classmethod
    def _request_id(cls, value: Any) -> str | None:
        if not isinstance(value, str) or len(value) > cls._MAX_REQUEST_ID_CHARS:
            return None
        try:
            return str(UUID(value))
        except (TypeError, ValueError, AttributeError):
            return None

    @staticmethod
    def _prompt(
        *,
        parent_task_id: str,
        objective: str,
        sensitivity: str,
        response_contract: str,
        source_request_id: str | None,
        references: Mapping[str, Any],
    ) -> str:
        source_id = source_request_id or "not_available"
        reference_text = json.dumps(
            dict(references), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return (
            "Act as an independent planning critic/normalizer. The previous Planner response was observed "
            "but failed only its response contract. Produce exactly one raw JSON object with a single "
            "`corrected_proposal` field containing a valid planning proposal. Do not emit Markdown fences, "
            "explanatory prose, comments, findings, approval, integration decisions, authority changes, "
            "budget changes, or policy relaxation. Preserve the requested parent_task_id and keep the "
            "proposal bounded. The Host will run the corrected proposal through deterministic validation; "
            "your output is proposal-only. Use this wrapper shape: "
            "{\"corrected_proposal\":{\"parent_task_id\":\"...\",\"rationale\":\"...\","
            "\"children\":[{\"child_key\":\"...\",\"objective\":\"...\","
            "\"task_type\":\"worker\"}]}}.\n"
            f"parent_task_id: {parent_task_id}\n"
            f"sensitivity: {sensitivity}\n"
            f"failed_response_contract: {response_contract}\n"
            f"failed_planner_request_id: {source_id}\n"
            f"objective:\n{objective}\n"
            f"reference_context:\n{reference_text}"
        )


def propose_with_planning_critic(
    planner: ModelPlanningAdapter,
    critic: ModelPlanningCriticAdapter | None,
    *,
    parent_task_id: str,
    objective: str,
    sensitivity: str = "normal",
    required_intelligence_tier: str | None = None,
    context_references: Mapping[str, Any] | None = None,
    host_validate: Callable[[RootPlanningProposal], Any] | None = None,
) -> RootPlanningProposal:
    """Compose one Planner request and at most one response-contract correction.

    Transport, quota, security, and unknown-effect failures propagate without
    invoking the Critic.  A corrected proposal is always offered to the
    caller's existing Host validator before this function returns when one is
    supplied; this helper never creates Tasks or mutates durable state.
    """

    if not isinstance(planner, ModelPlanningAdapter):
        raise TypeError("planner must be ModelPlanningAdapter")
    if critic is not None and not isinstance(critic, ModelPlanningCriticAdapter):
        raise TypeError("critic must be ModelPlanningCriticAdapter or None")
    try:
        proposal = planner.propose(
            parent_task_id=parent_task_id,
            objective=objective,
            sensitivity=sensitivity,
            required_intelligence_tier=required_intelligence_tier,
            context_references=context_references,
        )
    except PlanningResponseError as failure:
        if critic is None:
            raise
        proposal = critic.correct(
            parent_task_id=parent_task_id,
            objective=objective,
            sensitivity=sensitivity,
            planner_failure=failure,
            context_references=context_references,
        )
    if host_validate is not None:
        host_validate(proposal)
    return proposal


__all__ = [
    "ModelPlanningAdapter",
    "ModelPlanningCriticAdapter",
    "PLANNING_CRITIC_RESPONSE_SCHEMA",
    "PLANNING_PROPOSAL_RESPONSE_SCHEMA",
    "PlanningAdapterError",
    "PlanningCriticAdapterError",
    "PlanningResponseError",
    "propose_with_planning_critic",
]
