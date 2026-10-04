from types import SimpleNamespace

from src.dev_agent.operation import OperationProviderBinding, OperationService
from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.qualification import QualificationResolver
from src.dev_agent.resources.tier_authority import ProductionTierAuthority


def _qualification(tier=None):
    return SimpleNamespace(intelligence_tier=tier)


def _admission(tier):
    return SimpleNamespace(intelligence_tier=tier)


def test_remote_configuration_does_not_grant_a_production_tier():
    decision = ProductionTierAuthority().resolve(
        provider_id="gemini",
        provider_binding_id="gemini:core",
        model_id="gemini-3.8-flash",
        configured_tier="L2",
    )

    assert decision.eligible is False
    assert decision.tier is None
    assert decision.reason == "QUALIFICATION_MISSING"


def test_remote_current_qualification_beats_configuration_label():
    decision = ProductionTierAuthority().resolve(
        provider_id="gemini",
        provider_binding_id="gemini:core",
        model_id="gemini-3.8-flash",
        configured_tier="L2",
        qualification=_qualification("L1"),
    )

    assert decision.eligible is True
    assert decision.tier == "L1"
    assert decision.source == "qualification"


def test_remote_benchmark_without_current_qualification_is_not_a_grant():
    decision = ProductionTierAuthority().resolve(
        provider_id="gemini",
        provider_binding_id="gemini:core",
        model_id="gemini-3.8-flash",
        model_admission=_admission("L2"),
    )

    assert decision.eligible is False
    assert decision.tier is None
    assert decision.reason == "QUALIFICATION_MISSING"


def test_current_qualification_and_benchmark_can_grant_a_remote_tier():
    decision = ProductionTierAuthority().resolve(
        provider_id="gemini",
        provider_binding_id="gemini:core",
        model_id="gemini-3.8-flash",
        qualification=_qualification(),
        model_admission=_admission("L2"),
    )

    assert decision.eligible is True
    assert decision.tier == "L2"
    assert decision.source == "qualification+model_admission"


def test_conflicting_remote_tier_evidence_fails_closed():
    decision = ProductionTierAuthority().resolve(
        provider_id="gemini",
        provider_binding_id="gemini:core",
        model_id="gemini-3.8-flash",
        qualification=_qualification("L1"),
        model_admission=_admission("L2"),
    )

    assert decision.eligible is False
    assert decision.tier is None
    assert decision.reason == "TIER_EVIDENCE_CONFLICT"


def test_local_explicit_l1_policy_is_allowed_without_cloud_qualification():
    decision = ProductionTierAuthority().resolve(
        provider_id="ollama",
        provider_binding_id="ollama:local:qwen3.5-4b",
        model_id="qwen3.5:4b",
        configured_tier="L1",
    )

    assert decision.eligible is True
    assert decision.tier == "L1"
    assert decision.source == "local_policy"


def test_local_explicit_l2_is_not_silently_promoted():
    decision = ProductionTierAuthority().resolve(
        provider_id="ollama",
        provider_binding_id="ollama:local:qwen3.5-9b",
        model_id="qwen3.5:9b",
        configured_tier="L2",
    )

    assert decision.eligible is False
    assert decision.tier is None
    assert decision.reason == "LOCAL_TIER_REQUIRES_EVIDENCE"


def test_operation_does_not_persist_remote_configuration_as_production_tier(tmp_path):
    binding = OperationProviderBinding(
        provider_id="gemini",
        provider_binding_id="gemini:unqualified",
        model="gemini-unqualified",
        quota_domain="gemini:test",
        intelligence_tier="L2",
    )
    provider = SimpleNamespace(
        provider_binding_id=binding.binding_id,
        model_id=binding.model,
        intelligence_tier="L2",
    )

    with ResourceLedger(tmp_path / "tier-authority.sqlite3") as ledger:
        OperationService._ensure_resource(
            ledger,
            provider,
            binding,
            qualification_resolver=QualificationResolver(entries=[]),
        )
        resource = ledger.get_resource(binding.binding_id)

    assert resource["metadata"].get("intelligence_tier") is None
    assert resource["metadata"]["intelligence_tier_source"] == "none"
