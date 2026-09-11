"""Ollama Cloud adapter kept separate from the local Ollama provider."""

from ..openai_compatible import OpenAICompatibleHttpProvider


class OllamaCloudHttpProvider(OpenAICompatibleHttpProvider):
    """Ollama Cloud's OpenAI-compatible chat endpoint."""

    provider_id = "ollama_cloud"
    api_key_env = "OLLAMA_API_KEY"
    default_base_url = "https://ollama.com/v1"


__all__ = ["OllamaCloudHttpProvider"]
