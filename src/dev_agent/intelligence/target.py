"""Small, explicit boundary between model and external-agent execution.

The intelligence tier does not identify an execution mechanism.  An L3 task
may be satisfied by an L3 ModelProvider or, when the task explicitly requires
agent autonomy, by an approved AgentBackend.  This module only resolves that
choice from already-established authority evidence; it does not perform
routing, budgeting, approval, or backend dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from ..domain.protocol import IntelligenceTier


class ExecutionTarget(str, Enum):
    MODEL_PROVIDER = "model_provider"
    AGENT_BACKEND = "agent_backend"


class ExecutionTargetError(ValueError):
    """The requested execution target cannot be admitted safely."""


def _tier(value: IntelligenceTier | str) -> IntelligenceTier:
    try:
        return value if isinstance(value, IntelligenceTier) else IntelligenceTier(value)
    except (TypeError, ValueError) as exc:
        raise ExecutionTargetError("required_tier must be a valid intelligence tier") from exc


def _strict_bool(value: bool, name: str) -> None:
    if type(value) is not bool:
        raise ExecutionTargetError(f"{name} must be a boolean")


def _capabilities(values: Iterable[str], name: str) -> frozenset[str]:
    if isinstance(values, (str, bytes)):
        raise ExecutionTargetError(f"{name} must be a sequence of strings")
    try:
        normalized = frozenset(value.strip() for value in values)
    except (AttributeError, TypeError) as exc:
        raise ExecutionTargetError(f"{name} must be a sequence of strings") from exc
    if any(not value for value in normalized):
        raise ExecutionTargetError(f"{name} must contain non-empty strings")
    return normalized


@dataclass(frozen=True)
class ExecutionTargetDecision:
    """Auditable result of the target seam; no authority is minted here."""

    target: ExecutionTarget
    required_tier: IntelligenceTier
    reason: str


class ExecutionTargetPolicy:
    """Choose a target without turning an L3 tier into implicit autonomy.

    A normal task prefers a ModelProvider, even when an AgentBackend is
    available.  AgentBackend selection requires explicit autonomy intent,
    operator approval, budget admission, privacy admission, and capability
    coverage.  Callers must obtain those evidences from the existing Control
    Plane and pass them here; this policy never creates them.
    """

    def choose(
        self,
        *,
        required_tier: IntelligenceTier | str,
        required_autonomy: bool = False,
        required_capabilities: Iterable[str] = (),
        model_provider_available: bool,
        agent_backend_available: bool,
        agent_backend_approved: bool = False,
        agent_backend_capabilities: Iterable[str] = (),
        budget_admitted: bool = False,
        privacy_allowed: bool = False,
        explicit_target: ExecutionTarget | str | None = None,
    ) -> ExecutionTargetDecision:
        tier = _tier(required_tier)
        _strict_bool(required_autonomy, "required_autonomy")
        _strict_bool(model_provider_available, "model_provider_available")
        _strict_bool(agent_backend_available, "agent_backend_available")
        _strict_bool(agent_backend_approved, "agent_backend_approved")
        _strict_bool(budget_admitted, "budget_admitted")
        _strict_bool(privacy_allowed, "privacy_allowed")
        required = _capabilities(required_capabilities, "required_capabilities")
        backend_capabilities = _capabilities(agent_backend_capabilities, "agent_backend_capabilities")
        if not budget_admitted:
            raise ExecutionTargetError("budget admission is required")
        if not privacy_allowed:
            raise ExecutionTargetError("privacy admission is required")

        target: ExecutionTarget | None
        if explicit_target is None:
            target = None
        else:
            try:
                target = explicit_target if isinstance(explicit_target, ExecutionTarget) else ExecutionTarget(explicit_target)
            except (TypeError, ValueError) as exc:
                raise ExecutionTargetError("explicit_target must be model_provider or agent_backend") from exc

        if required_autonomy and target is ExecutionTarget.MODEL_PROVIDER:
            raise ExecutionTargetError("required autonomy cannot use a ModelProvider")
        if required_autonomy:
            target = ExecutionTarget.AGENT_BACKEND
        elif target is None:
            target = ExecutionTarget.MODEL_PROVIDER

        if target is ExecutionTarget.MODEL_PROVIDER:
            if not model_provider_available:
                raise ExecutionTargetError("ModelProvider is unavailable; AgentBackend requires explicit autonomy")
            return ExecutionTargetDecision(target=target, required_tier=tier, reason="minimum_sufficient_model_provider")

        if not agent_backend_available:
            raise ExecutionTargetError("AgentBackend is unavailable")
        if not agent_backend_approved:
            raise ExecutionTargetError("AgentBackend approval is required")
        missing = required - backend_capabilities
        if missing:
            raise ExecutionTargetError(f"AgentBackend capabilities are insufficient: {', '.join(sorted(missing))}")
        return ExecutionTargetDecision(target=target, required_tier=tier, reason="explicit_approved_agent_autonomy")


__all__ = ["ExecutionTarget", "ExecutionTargetDecision", "ExecutionTargetError", "ExecutionTargetPolicy"]
