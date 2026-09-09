from src.dev_agent import providers


def test_provider_package_exports_all_provider_and_dispatch_symbols():
    exported = set(providers.__all__)

    assert {"ModelProvider", "GroqHttpProvider", "MistralHttpProvider", "OpenRouterHttpProvider", "ProviderDispatchJournal", "SambaNovaHttpProvider", "ProviderDispatcher", "ProviderRegistry"} <= exported
    assert len(providers.__all__) == len(set(providers.__all__))
