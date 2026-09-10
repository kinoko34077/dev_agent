from pathlib import Path

from src.dev_agent.policy import PathPolicy
from src.dev_agent.security.audit import AuditRecorder
from src.dev_agent.security.protected_paths import is_protected_path


def test_path_policy_uses_the_most_specific_matching_rule():
    workspace = Path("/tmp/dev-agent-policy")
    policy = PathPolicy(
        workspace,
        {
            "": {"read", "write"},
            "private": set(),
        },
    )

    assert policy.check(workspace / "README.md", "write") is True
    assert policy.check(workspace / "private" / "key.txt", "read") is False
    assert policy.check(workspace / "private" / "key.txt", "write") is False


def test_protected_authority_policy_covers_responsibility_paths():
    assert is_protected_path("src/dev_agent/resources/budget.py")
    assert is_protected_path("src/dev_agent/resources/budget_store.py")
    assert is_protected_path("src/dev_agent/resources/control.py")
    assert is_protected_path("config/v2.yaml")
    assert is_protected_path("src/dev_agent/security/audit.py")
    assert is_protected_path("recovery/validate_sqlite_state.py")


def test_audit_keeps_usage_and_session_telemetry_but_redacts_secret_fields():
    payload = AuditRecorder.sanitize_payload(
        {
            "input_tokens": 12,
            "output_tokens": 8,
            "token_limit": 1000,
            "session_id": "session-123",
            "api_token": "secret-value",
            "nested": {"authorization": "Bearer secret-value"},
        }
    )

    assert payload["input_tokens"] == 12
    assert payload["output_tokens"] == 8
    assert payload["token_limit"] == 1000
    assert payload["session_id"] == "session-123"
    assert payload["api_token"] == "[REDACTED]"
    assert payload["nested"]["authorization"] == "[REDACTED]"
