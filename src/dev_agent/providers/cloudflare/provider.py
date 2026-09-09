"""Injected-transport Cloudflare Workers AI adapter.

Endpoint and authentication details remain in the injected transport. The
adapter exposes only the normalized Provider contract to the Kernel.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ...domain.protocol import ModelResponse
from ..openai_compatible import OpenAICompatibleProvider


class CloudflareWorkersAIProvider(OpenAICompatibleProvider):
    provider_id = "cloudflare"

    def __init__(self, transport: Callable[[dict[str, Any]], ModelResponse | Mapping[str, Any]], *, model: str = "cloudflare") -> None:
        super().__init__(transport, model=model)

