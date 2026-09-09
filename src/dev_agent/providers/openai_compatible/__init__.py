"""Independent OpenAI-compatible JSON adapter."""

from .http import OpenAICompatibleHttpProvider, OpenAICompatibleHttpTransport
from .provider import OpenAICompatibleProvider

__all__ = ["OpenAICompatibleHttpProvider", "OpenAICompatibleHttpTransport", "OpenAICompatibleProvider"]
