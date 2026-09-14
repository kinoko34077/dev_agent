"""Development-only MCP binding over the existing Commander/Supervisor APIs.

This module is a composition boundary, not another execution engine.  The
transport-neutral :class:`McpRuntimeAdapter` performs request/result bounds,
redaction, approval gating, and UNKNOWN mapping; this binding only supplies
the already-existing Supervisor operations for one explicitly selected run.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from scripts.devfarm import DevFarmError
from scripts.devfarm_artifacts import artifact_reference
from scripts.devfarm_supervisor import (
    CodexSupervisedCommanderRun,
    latest_rework_decision,
    providers_for_resume,
)
from src.dev_agent.mcp import (
    McpAuthorizer,
    McpRejected,
    McpRuntimeAdapter,
    McpToolName,
)


def _required_text(arguments: Mapping[str, Any], name: str, *, maximum: int = 4096) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise McpRejected("INVALID_ARGUMENTS")
    return value.strip()


def _optional_text(arguments: Mapping[str, Any], name: str, *, maximum: int = 4096) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise McpRejected("INVALID_ARGUMENTS")
    return value.strip()


class SupervisorMcpBinding:
    """Bind one run to existing Host-owned Supervisor operations.

    The run id and repository root are fixed at construction time.  Provider
    objects, trust level, operator approval, and the integration checkout are
    also composition inputs, so an MCP caller cannot select credentials or
    escalate Host execution by putting them in a request body.
    """

    def __init__(
        self,
        root: str | Path,
        run_id: str,
        *,
        providers: Mapping[str, Any] | None = None,
        orchestrator: Any | None = None,
        trust_level: str = "STATIC_ONLY",
        operator_approved: bool = False,
        integration_checkout: str | Path | None = None,
        authorize: McpAuthorizer | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.run_id = _required_text({"run_id": run_id}, "run_id", maximum=128)
        if trust_level not in {"STATIC_ONLY", "TRUSTED_HOST_EXEC", "OS_SANDBOXED"}:
            raise ValueError("unsupported verification trust level")
        if not isinstance(operator_approved, bool):
            raise TypeError("operator_approved must be a boolean")
        if integration_checkout is not None:
            checkout = Path(integration_checkout).resolve()
            if not checkout.is_dir():
                raise ValueError("integration_checkout must be an existing directory")
        else:
            checkout = None
        self._runner = CodexSupervisedCommanderRun(self.root, self.run_id)
        self._providers = dict(providers) if providers is not None else None
        self._orchestrator = orchestrator
        self._trust_level = trust_level
        self._operator_approved = operator_approved
        self._integration_checkout = checkout
        self._adapter = McpRuntimeAdapter(
            {
                McpToolName.STATUS: self._status,
                McpToolName.ARTIFACT_SUMMARY: self._artifact_summary,
                McpToolName.RUN: self._run,
                McpToolName.RESUME: self._resume,
                McpToolName.REVIEW: self._review,
                McpToolName.REWORK: self._rework,
                McpToolName.INTEGRATE: self._integrate,
            },
            authorize=authorize,
        )

    @property
    def adapter(self) -> McpRuntimeAdapter:
        return self._adapter

    def invoke(self, request: Mapping[str, Any]):
        """Forward one bounded MCP request to the transport-neutral adapter."""

        return self._adapter.invoke(request)

    def _check_run(self, arguments: Mapping[str, Any]) -> None:
        requested = arguments.get("run_id")
        if requested is not None and requested != self.run_id:
            raise McpRejected("RUN_ID_MISMATCH")

    def _status(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        self._check_run(arguments)
        return {"data": self._runner.status().to_dict()}

    def _artifact_summary(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        self._check_run(arguments)
        task_id = _optional_text(arguments, "task_id", maximum=128)
        attempt_id = _optional_text(arguments, "attempt_id", maximum=128)
        packets = []
        for packet in self._runner.status().review_packets:
            if task_id is not None and packet.get("task_id") != task_id:
                continue
            if attempt_id is not None and packet.get("attempt_id") != attempt_id:
                continue
            packets.append(dict(packet))
        return {"data": {"run_id": self.run_id, "review_packets": packets}}

    def _execution_kwargs(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        self._check_run(arguments)
        timeout = arguments.get("timeout_seconds", 30.0)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0 or timeout > 900:
            raise McpRejected("INVALID_ARGUMENTS")
        providers = self._providers
        if providers is None:
            providers = providers_for_resume(self.root, self.run_id, float(timeout))
        return {
            "providers": providers,
            "orchestrator": self._orchestrator,
            "verification_trust_level": self._trust_level,
            "operator_approved": self._operator_approved,
            "dispatch_timeout_seconds": self._bounded_number(arguments, "dispatch_timeout_seconds", 300.0, maximum=900.0),
        }

    @staticmethod
    def _bounded_number(arguments: Mapping[str, Any], name: str, default: float, *, maximum: float) -> float:
        value = arguments.get(name, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0 or value > maximum:
            raise McpRejected("INVALID_ARGUMENTS")
        return float(value)

    def _run(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        kwargs = self._execution_kwargs(arguments)
        kwargs["expected_remaining_seconds"] = arguments.get("expected_remaining_seconds")
        kwargs["max_wait_seconds"] = self._bounded_number(arguments, "max_wait_seconds", 900.0, maximum=900.0)
        return {"data": self._runner.run_until_intervention(**kwargs).to_dict()}

    def _resume(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        kwargs = self._execution_kwargs(arguments)
        kwargs["expected_remaining_seconds"] = arguments.get("expected_remaining_seconds")
        return {"data": self._runner.advance(**kwargs).to_dict()}

    def _review(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        self._check_run(arguments)
        task_id = _required_text(arguments, "task_id", maximum=128)
        attempt_id = _required_text(arguments, "attempt_id", maximum=128)
        decision = _required_text(arguments, "decision", maximum=64)
        findings = arguments.get("findings", [])
        if isinstance(findings, (str, bytes)) or not isinstance(findings, list) or len(findings) > 32:
            raise McpRejected("INVALID_ARGUMENTS")
        normalized_findings = []
        for item in findings:
            if not isinstance(item, str) or not item.strip() or len(item.strip()) > 20_000:
                raise McpRejected("INVALID_ARGUMENTS")
            normalized_findings.append(item.strip())
        evidence_refs = arguments.get("evidence_refs", [])
        if isinstance(evidence_refs, (str, bytes)) or not isinstance(evidence_refs, list) or len(evidence_refs) > 32:
            raise McpRejected("INVALID_ARGUMENTS")
        normalized_refs = []
        for item in evidence_refs:
            if not isinstance(item, Mapping):
                raise McpRejected("INVALID_ARGUMENTS")
            normalized_refs.append(dict(item))
        try:
            return {"data": self._runner.record_review_decision(
                task_id,
                attempt_id=attempt_id,
                decision=decision,
                findings=normalized_findings,
                evidence_refs=normalized_refs,
                required_correction=_optional_text(arguments, "required_correction", maximum=20_000),
                reviewer_role=_optional_text(arguments, "reviewer_role", maximum=128) or "mcp_reviewer",
            ).to_dict()}
        except DevFarmError as exc:
            raise McpRejected("AUTHORITY_REJECTED") from exc

    def _rework(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        self._check_run(arguments)
        task_id = _required_text(arguments, "task_id", maximum=128)
        failure_ref = _required_text(arguments, "failure_evidence_ref", maximum=2048)
        findings_ref = _optional_text(arguments, "review_findings_ref", maximum=2048)
        plan = self._runner.plan()
        task = next((item for item in plan["tasks"] if item["task_id"] == task_id), None)
        if task is None:
            raise McpRejected("TASK_NOT_FOUND")
        attempt_id = task.get("last_attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            raise McpRejected("ATTEMPT_NOT_FOUND")
        try:
            decision = latest_rework_decision(plan, task_id, attempt_id)
            correction = decision.get("required_correction")
            if not isinstance(correction, str) or not correction.strip():
                raise McpRejected("CORRECTION_NOT_FOUND")
            requested = _optional_text(arguments, "required_correction", maximum=20_000)
            if requested is not None and requested != correction:
                raise McpRejected("CORRECTION_MISMATCH")
            assignment = task.get("assignment")
            if not isinstance(assignment, Mapping):
                raise McpRejected("ASSIGNMENT_NOT_FOUND")
            handoff = self._runner.rework_handoff(
                task_id,
                failure_evidence_reference=artifact_reference(failure_ref, kind="failure_evidence"),
                review_findings_reference=(
                    artifact_reference(findings_ref, kind="review_findings") if findings_ref else None
                ),
                required_correction=correction,
            )
            return {"data": self._runner.reassign(
                task_id,
                provider_id=_required_text(assignment, "provider_id", maximum=128),
                model_id=_required_text(assignment, "model_id", maximum=256),
                provider_binding_id=assignment.get("provider_binding_id"),
                rework_handoff=handoff,
            ).to_dict()}
        except DevFarmError as exc:
            raise McpRejected("AUTHORITY_REJECTED") from exc

    def _integrate(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        self._check_run(arguments)
        if self._integration_checkout is None:
            raise McpRejected("INTEGRATION_CHECKOUT_NOT_BOUND")
        try:
            return {"data": self._runner.integrate_approved_worker(
                _required_text(arguments, "task_id", maximum=128),
                decision_id=_required_text(arguments, "decision_id", maximum=128),
                commit_message=_required_text(arguments, "commit_message", maximum=200),
                target_checkout=self._integration_checkout,
                target_ref=_required_text(arguments, "target_ref", maximum=256),
            ).to_dict()}
        except DevFarmError as exc:
            raise McpRejected("INTEGRATION_REJECTED") from exc


__all__ = ["SupervisorMcpBinding"]
