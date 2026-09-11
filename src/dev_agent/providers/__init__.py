"""Provider adapter boundaries with lazy compatibility exports.

Internal modules should import leaf modules directly.  The public package
names remain available through ``__getattr__`` so existing consumers keep
their API without importing every adapter just to use one Provider.
"""

from importlib import import_module

from .base import ModelProvider, ProviderError

_LAZY_EXPORTS = {
    "CloudflareWorkersAIHttpProvider": (".cloudflare", "CloudflareWorkersAIHttpProvider"),
    "CloudflareWorkersAIProvider": (".cloudflare", "CloudflareWorkersAIProvider"),
    "GeminiHttpProvider": (".gemini", "GeminiHttpProvider"),
    "GeminiProvider": (".gemini", "GeminiProvider"),
    "GroqHttpProvider": (".groq", "GroqHttpProvider"),
    "GroqProvider": (".groq", "GroqProvider"),
    "MistralProvider": (".mistral", "MistralProvider"),
    "MistralHttpProvider": (".mistral", "MistralHttpProvider"),
    "OllamaProvider": (".ollama", "OllamaProvider"),
    "OllamaCloudHttpProvider": (".ollama_cloud", "OllamaCloudHttpProvider"),
    "OpenAICompatibleProvider": (".openai_compatible", "OpenAICompatibleProvider"),
    "OpenAICompatibleHttpProvider": (".openai_compatible", "OpenAICompatibleHttpProvider"),
    "OpenAICompatibleHttpTransport": (".openai_compatible", "OpenAICompatibleHttpTransport"),
    "OpenRouterFreeProvider": (".openrouter", "OpenRouterFreeProvider"),
    "OpenRouterHttpProvider": (".openrouter", "OpenRouterHttpProvider"),
    "SambaNovaHttpProvider": (".sambanova", "SambaNovaHttpProvider"),
    "SambaNovaProvider": (".sambanova", "SambaNovaProvider"),
    "VercelAIGatewayHttpProvider": (".vercel", "VercelAIGatewayHttpProvider"),
    "DispatchAudit": (".dispatch", "DispatchAudit"),
    "ProviderDispatcher": (".dispatch", "ProviderDispatcher"),
    "ProviderDefinition": (".factory", "ProviderDefinition"),
    "ProviderFactory": (".factory", "ProviderFactory"),
    "ProviderDispatchJournal": (".journal", "ProviderDispatchJournal"),
    "ProviderRegistry": (".registry", "ProviderRegistry"),
}


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

__all__ = [
    "CloudflareWorkersAIHttpProvider",
    "CloudflareWorkersAIProvider",
    "DispatchAudit",
    "GeminiHttpProvider",
    "GeminiProvider",
    "GroqHttpProvider",
    "GroqProvider",
    "MistralProvider",
    "MistralHttpProvider",
    "ModelProvider",
    "OllamaProvider",
    "OllamaCloudHttpProvider",
    "OpenAICompatibleProvider",
    "OpenRouterFreeProvider",
    "OpenRouterHttpProvider",
    "ProviderDispatcher",
    "ProviderDefinition",
    "ProviderFactory",
    "ProviderDispatchJournal",
    "ProviderError",
    "ProviderRegistry",
    "SambaNovaHttpProvider",
    "SambaNovaProvider",
    "VercelAIGatewayHttpProvider",
    "OpenAICompatibleHttpProvider",
    "OpenAICompatibleHttpTransport",
]
