"""Provider adapter boundaries."""

from .base import ModelProvider, ProviderError
from .gemini import GeminiHttpProvider, GeminiProvider
from .ollama import OllamaProvider
from .openai_compatible import OpenAICompatibleProvider

__all__ = ["GeminiHttpProvider", "GeminiProvider", "ModelProvider", "OllamaProvider", "OpenAICompatibleProvider", "ProviderError"]
from .dispatch import DispatchAudit, ProviderDispatcher, ProviderRegistry

__all__ = ["DispatchAudit", "ProviderDispatcher", "ProviderRegistry"]
