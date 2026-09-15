from types import SimpleNamespace

from scripts.devfarm_artifacts import artifact_reference, attempt_id, bounded_test_output
import pytest

from scripts.devfarm_resource_pool import (
    ResourcePoolError,
    admit_resource_pool,
    make_binding,
    resolve_provider_pool,
)
from scripts.devfarm_verification import validate_host_test_targets


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


def test_resource_pool_resolver_accepts_explicit_non_secret_pool_json():
    bindings = resolve_provider_pool(
        pool_json='[{"provider_id":"fake","model":"model-a","provider_binding_id":"fake:a","api_key_env":"FAKE_A","quota_domain":"fake:quota","timeout_seconds":5}]',
        use_configured_pool=False,
    )

    assert bindings is not None
    assert [(item.binding_id, item.model) for item in bindings] == [("fake:a", "model-a")]
    assert "FAKE_A" in repr(bindings[0])


def test_resource_pool_resolver_uses_explicit_configured_metadata_without_secret_values():
    values = {
        "GEMINI_API_KEY_3": "secret-value",
        "GEMINI_PROJECT_ID_3": "ignored",
        "GEMINI_MODEL_3": "gemini-test",
    }

    bindings = resolve_provider_pool(
        pool_json=None,
        use_configured_pool=True,
        env=values.get,
    )

    assert bindings is not None
    selected = next(item for item in bindings if item.binding_id == "gemini:worker:free-3")
    assert selected.model == "gemini-test"
    assert "secret-value" not in repr(selected)


def test_resource_pool_resolver_rejects_ambiguous_or_malformed_sources():
    with pytest.raises(ResourcePoolError, match="cannot be combined"):
        resolve_provider_pool(
            pool_json='[{"provider_id":"fake","model":"model-a"}]',
            use_configured_pool=True,
        )
    with pytest.raises(ResourcePoolError, match="invalid binding object"):
        resolve_provider_pool(pool_json="[1]", use_configured_pool=False)


def test_public_artifact_and_verification_boundaries_are_bounded(tmp_path):
    assert artifact_reference(".devfarm/results/task/result.json", kind="result") == {
        "kind": "result",
        "path": ".devfarm/results/task/result.json",
    }
    assert attempt_id("attempt-1") == "attempt-1"
    bounded = bounded_test_output("ok")
    assert bounded == {"text": "ok", "truncated": False}

    validate_host_test_targets(tmp_path, ["python", "-m", "pytest", "tests/v2/test_target.py", "-q"])
