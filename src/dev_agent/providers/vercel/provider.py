"""Vercel AI Gateway OpenAI-compatible adapter."""

from ..openai_compatible import OpenAICompatibleHttpProvider


class VercelAIGatewayHttpProvider(OpenAICompatibleHttpProvider):
    """Vercel AI Gateway's OpenAI-compatible chat endpoint."""

    provider_id = "vercel"
    api_key_env = "AI_GATEWAY_API_KEY"
    default_base_url = "https://ai-gateway.vercel.sh/v1"


__all__ = ["VercelAIGatewayHttpProvider"]
