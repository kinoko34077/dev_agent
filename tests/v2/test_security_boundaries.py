from pathlib import Path

import pytest

from src.dev_agent.policy import PathPolicy
from src.dev_agent.security.audit import AuditRecorder
from src.dev_agent.security.protected_paths import is_protected_path
from src.dev_agent.tools.registry import KNOWN_SIDE_EFFECT_LEVELS, ToolSpec

pytestmark = pytest.mark.security


def _minimal_handler(_args):
    return {}


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
    # Provider / credential authority
    assert is_protected_path("src/dev_agent/resources/provider_authority_constants.py")
    assert is_protected_path("src/dev_agent/resources/provider_policy.py")
    assert is_protected_path("src/dev_agent/providers/factory.py")
    # Billing / budget authority
    assert is_protected_path("src/dev_agent/resources/budget.py")
    assert is_protected_path("src/dev_agent/resources/budget_store.py")
    assert is_protected_path("src/dev_agent/resources/billing_catalog.py")
    assert is_protected_path("src/dev_agent/resources/quota_policy.py")
    # Qualification / routing authority
    assert is_protected_path("src/dev_agent/resources/qualification.py")
    assert is_protected_path("src/dev_agent/resources/control.py")
    assert is_protected_path("src/dev_agent/resources/router.py")
    assert is_protected_path("src/dev_agent/resources/repair.py")
    # Approval / permission authority
    assert is_protected_path("src/dev_agent/policy/approvals.py")
    assert is_protected_path("src/dev_agent/policy/permissions.py")
    # Tool side-effect / effect guard authority
    assert is_protected_path("src/dev_agent/tools/registry.py")
    assert is_protected_path("src/dev_agent/tools/runtime.py")
    assert is_protected_path("src/dev_agent/tools/effect_guard.py")
    assert is_protected_path("src/dev_agent/state/effects_repository.py")
    # Operation / bootstrap authority
    assert is_protected_path("src/dev_agent/operation.py")
    assert is_protected_path("src/dev_agent/operation_bootstrap.py")
    # AgentBackend dispatch
    assert is_protected_path("src/dev_agent/backends/dispatcher.py")
    # Legacy checks
    assert is_protected_path("spec/v2/PROVIDER_CAPABILITY_MATRIX.json")
    assert is_protected_path("config/v2.yaml")
    assert is_protected_path("scripts/devfarm_worker.py")
    assert is_protected_path("src/dev_agent/security/audit.py")
    assert is_protected_path("recovery/validate_sqlite_state.py")
    assert is_protected_path(".github/workflows/v2-core.yml")
    assert is_protected_path("credentials.json")
    assert is_protected_path("keys/deploy.key")
    assert is_protected_path("config/service_api_key.json")


def test_tool_spec_rejects_unknown_side_effect_level():
    """Typos / unknown levels must be caught at ToolSpec construction."""
    with pytest.raises(ValueError, match="side_effect_level"):
        ToolSpec(name="bad", description="d", handler=_minimal_handler, side_effect_level="finanical")
    with pytest.raises(ValueError, match="side_effect_level"):
        ToolSpec(name="bad", description="d", handler=_minimal_handler, side_effect_level="")
    with pytest.raises(ValueError, match="side_effect_level"):
        ToolSpec(name="bad", description="d", handler=_minimal_handler, side_effect_level="unknown")


def test_tool_spec_accepts_all_known_side_effect_levels():
    for level in KNOWN_SIDE_EFFECT_LEVELS - {"process"}:
        spec = ToolSpec(name=f"tool-{level}", description="d", handler=_minimal_handler, side_effect_level=level)
        assert spec.side_effect_level == level
    # "process" requires subprocess isolation
    spec = ToolSpec(
        name="proc",
        description="d",
        handler=_minimal_handler,
        side_effect_level="process",
        isolation="subprocess",
        trust_level="untrusted",
    )
    assert spec.side_effect_level == "process"


def test_provider_authority_constants_are_single_source_of_truth():
    """provider_policy and recovery/validate_resources both derive from provider_authority_constants."""
    from src.dev_agent.resources.provider_authority_constants import LOCAL_PROVIDER_IDS
    from src.dev_agent.resources import provider_policy

    # provider_policy uses the same frozenset
    assert provider_policy._LOCAL_PROVIDERS is LOCAL_PROVIDER_IDS or provider_policy._LOCAL_PROVIDERS == LOCAL_PROVIDER_IDS


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
