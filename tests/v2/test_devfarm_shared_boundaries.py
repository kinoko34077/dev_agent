from types import SimpleNamespace

from scripts.devfarm_resource_pool import admit_resource_pool, make_binding


def test_resource_pool_binding_factory_keeps_exact_execution_identity():
    binding = make_binding(
        provider_id="fake",
        binding_id="fake:project-a",
        model_id="model-a",
        api_key_env="FAKE_API_KEY",
        quota_domain="fake:project-a",
        timeout_seconds=12.5,
    )

    assert binding.provider_id == "fake"
    assert binding.binding_id == "fake:project-a"
    assert binding.model == "model-a"
    assert binding.api_key_env == "FAKE_API_KEY"
    assert binding.quota_domain == "fake:project-a"
    assert binding.timeout_seconds == 12.5


def test_resource_pool_admission_is_role_neutral(monkeypatch):
    binding = make_binding(
        provider_id="fake",
        binding_id="fake:project-a",
        model_id="model-a",
        api_key_env="FAKE_API_KEY",
        quota_domain="fake:project-a",
        timeout_seconds=12.5,
    )
    qualification = SimpleNamespace(
        routing_capabilities=frozenset({"text"}),
        intelligence_tier="L2",
    )
    profile = SimpleNamespace(no_charge_guaranteed=True)

    class Resolver:
        def resolve(self, provider_id, binding_id, model, **kwargs):
            assert (provider_id, binding_id, model) == ("fake", "fake:project-a", "model-a")
            assert kwargs["min_confidence"] == "high"
            return qualification

    monkeypatch.setattr(
        "scripts.devfarm_resource_pool.default_profile_for",
        lambda *_args: profile,
    )

    admitted = admit_resource_pool((binding,), resolver=Resolver(), required_tier="L2")

    assert admitted == ((binding, qualification, profile),)
