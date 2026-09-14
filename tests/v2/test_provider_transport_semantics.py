from __future__ import annotations

from src.dev_agent.providers.base import (
    ProviderError,
    TransportFailureCategory,
    classify_transport_failure,
)


class _WinSockDenied(OSError):
    def __init__(self) -> None:
        super().__init__(10013, "permission denied")
        self.winerror = 10013


def test_explicit_codex_sandbox_evidence_classifies_winerror_10013_without_changing_reconciliation():
    error = ProviderError("provider transport failed", category="transport", retryable=True)
    error.__cause__ = _WinSockDenied()

    assert classify_transport_failure(error, execution_boundary="codex_sandbox") is TransportFailureCategory.SANDBOX_NETWORK_DENIED
    assert error.retryable is True
    assert error.requires_reconciliation is True


def test_explicit_host_process_evidence_classifies_winerror_10013_as_local_policy_denial():
    error = ProviderError("provider transport failed", category="transport", retryable=True)
    error.__cause__ = _WinSockDenied()

    assert classify_transport_failure(error, execution_boundary="host_process") is TransportFailureCategory.LOCAL_NETWORK_POLICY_DENIED


def test_unknown_execution_boundary_does_not_claim_sandbox_or_local_policy():
    error = ProviderError("provider transport failed", category="transport", retryable=True)
    error.__cause__ = _WinSockDenied()

    assert classify_transport_failure(error) is TransportFailureCategory.TRANSPORT_UNCLASSIFIED


def test_confirmed_provider_transport_failure_is_distinct_from_local_denial():
    error = ProviderError("provider transport failed", category="transport", retryable=True)

    assert classify_transport_failure(error, execution_boundary="provider_process") is TransportFailureCategory.PROVIDER_TRANSPORT_FAILURE
