"""Gemini adapter; the Gemini SDK remains outside the Core protocol."""

from .provider import GeminiHttpProvider, GeminiProvider

__all__ = ["GeminiHttpProvider", "GeminiProvider"]
