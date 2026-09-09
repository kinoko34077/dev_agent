from src.dev_agent.providers.dispatch import ProviderDispatcher
from src.dev_agent.runtime.controller import Controller


def test_provider_runtime_path_roles_are_explicit():
    assert ProviderDispatcher.PROVIDER_PATH_ROLE == "canonical_dispatcher_registry"
    assert Controller.DIRECT_PROVIDER_PATH_ROLE == "compatibility_legacy"
