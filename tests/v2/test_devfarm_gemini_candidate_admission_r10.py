import pytest
from scripts.devfarm_provider_runtime import build_worker_provider
from scripts.devfarm_worker_admission import validate_worker_provider

@pytest.mark.current_billing_catalog
def test_gemini_free_candidate_is_proposal_only():
    provider = build_worker_provider("gemini", "gemini-3.6-flash", 30.0, "gemini:worker:free-3")
    provider_id, model_id, binding_id, tier, eligibility = validate_worker_provider(provider)
    assert tier == "L1"
    assert eligibility.capability_qualified is False
    assert eligibility.billing_admitted is True
    assert eligibility.reason == "proposal_only_candidate"
    assert provider.proposal_only_candidate is True
