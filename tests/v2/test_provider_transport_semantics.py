from __future__ import annotations

import socket
import ssl

import pytest

from src.dev_agent.providers.base import (
    ProviderError,
    TransportFailureCategory,
    TransportStage,
    annotate_transport_failure,
    classify_transport_failure,
    project_transport_failure,
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


@pytest.mark.parametrize(
    ("cause", "expected_category", "expected_stage"),
    [
        (socket.gaierror(-2, "name resolution failed"), TransportFailureCategory.DNS_FAILURE, TransportStage.RESOLVE),
        (ConnectionRefusedError(111, "connection refused"), TransportFailureCategory.CONNECT_FAILURE, TransportStage.CONNECT),
        (ConnectionResetError(104, "connection reset"), TransportFailureCategory.CONNECTION_RESET, TransportStage.RESPONSE_WAIT),
        (ssl.SSLError("TLS handshake failed"), TransportFailureCategory.TLS_FAILURE, TransportStage.TLS),
    ],
)
def test_transport_diagnostics_project_bounded_exception_taxonomy(cause, expected_category, expected_stage):
    error = ProviderError("provider transport failed", category="transport", retryable=True)
    error.__cause__ = cause

    projection = project_transport_failure(error, stage=expected_stage)

    assert projection["transport_failure_category"] == expected_category.value
    assert projection["transport_stage"] == expected_stage.value
    assert projection["transport_exception_type"] == type(cause).__name__
    assert "message" not in projection
    assert "name resolution failed" not in str(projection)
    assert error.requires_reconciliation is True


def test_timeout_diagnostic_uses_explicit_read_stage_without_changing_reconciliation():
    error = ProviderError("provider transport failed", category="transport", retryable=True)
    error.__cause__ = TimeoutError("private timeout detail")

    annotate_transport_failure(error, stage=TransportStage.RESPONSE_READ)

    assert getattr(error, "transport_failure_category") == TransportFailureCategory.READ_TIMEOUT.value
    assert getattr(error, "transport_stage") == TransportStage.RESPONSE_READ.value
    assert getattr(error, "transport_exception_type") == "TimeoutError"
    assert "private timeout detail" not in str(project_transport_failure(error))
    assert error.failover_safe is False
    assert error.requires_reconciliation is True


def test_transport_diagnostics_preserves_explicit_boundary_category_and_safe_numbers():
    error = ProviderError("provider transport failed", category="transport", retryable=True)
    error.__cause__ = OSError(10013, "permission denied")

    annotate_transport_failure(error, execution_boundary="host_process", stage=TransportStage.CONNECT)
    projection = project_transport_failure(error)

    assert projection == {
        "transport_failure_category": TransportFailureCategory.LOCAL_NETWORK_POLICY_DENIED.value,
        "transport_stage": TransportStage.CONNECT.value,
        "transport_exception_type": "OSError",
        "transport_errno": 10013,
        "transport_winerror": None,
    }
