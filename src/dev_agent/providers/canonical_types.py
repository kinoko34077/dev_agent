"""Single source of truth: provider_id -> the one canonical concrete adapter type.

ProviderFactory constructs a provider instance by looking up this mapping.
resources/provider_policy.py's validate_provider_instance_authority()
re-validates an already-constructed instance's *exact* type against the
same mapping. Both consumers derive from this one table so they cannot
diverge.

Only the finished HTTP adapter is canonical for production dispatch.
Injected-transport adapters (e.g. GeminiProvider, GroqProvider --
constructor takes an arbitrary ``transport`` callable) are a separate,
non-canonical construction path: they exist for offline/SDK-injected
testing and are deliberately excluded here, because "the class accepts any
callable and forwards to it" is exactly the shape a production Authority
boundary must not trust.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

# provider_id -> (module path relative to this package, exact class name)
CANONICAL_PROVIDER_MODULES: dict[str, tuple[str, str]] = {
    "cloudflare": (".cloudflare", "CloudflareWorkersAIHttpProvider"),
    "gemini": (".gemini", "GeminiHttpProvider"),
    "groq": (".groq", "GroqHttpProvider"),
    "mistral": (".mistral", "MistralHttpProvider"),
    "ollama": (".ollama", "OllamaProvider"),
    "openrouter": (".openrouter", "OpenRouterHttpProvider"),
    "sambanova": (".sambanova", "SambaNovaHttpProvider"),
    "ollama_cloud": (".ollama_cloud", "OllamaCloudHttpProvider"),
    "vercel": (".vercel", "VercelAIGatewayHttpProvider"),
}


def canonical_class(provider_id: str) -> type[Any] | None:
    """Return the exact canonical class for provider_id, or None if unmapped.

    None means "no canonical adapter is registered for this identity" --
    callers in an Authority context (validate_provider_instance_authority)
    must treat that as fail-closed rejection for any network-capable
    provider_id, not as "no restriction applies".
    """
    target = CANONICAL_PROVIDER_MODULES.get(provider_id)
    if target is None:
        return None
    module_name, class_name = target
    module = import_module(module_name, __package__)
    return getattr(module, class_name)


__all__ = ["CANONICAL_PROVIDER_MODULES", "canonical_class"]
