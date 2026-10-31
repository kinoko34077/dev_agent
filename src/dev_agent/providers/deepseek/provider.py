"""DeepSeek API adapter using the shared OpenAI-compatible HTTP boundary."""

from ..openai_compatible import OpenAICompatibleHttpProvider


class DeepSeekHttpProvider(OpenAICompatibleHttpProvider):
    """DeepSeek Chat Completions adapter with the provider's fixed API origin."""

    provider_id = "deepseek"
    api_key_env = "DEEPSEEK_API_KEY"
    default_base_url = "https://api.deepseek.com"
    max_output_tokens_field = "max_tokens"


__all__ = ["DeepSeekHttpProvider"]
