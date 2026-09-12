"""Configuration-only construction of concrete Provider adapters."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
import math
from typing import Any

from ..domain.protocol import IntelligenceTier
from ..resources.provider_policy import validate_api_key_env_authority, validate_endpoint_authority
from .canonical_types import CANONICAL_PROVIDER_MODULES


@dataclass(frozen=True)
class ProviderDefinition:
    """Non-secret Provider construction settings.

    API keys are deliberately resolved by each adapter from its external
    environment; they cannot be embedded in this definition.
    """

    provider_id: str
    model: str
    base_url: str | None = None
    timeout_seconds: float = 30.0
    provider_binding_id: str | None = None
    credential_id: str | None = None
    api_key_env: str | None = None
    project_id: str | None = None
    intelligence_tier: str | IntelligenceTier | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or not self.provider_id.strip():
            raise ValueError("provider_id must be a non-empty string")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model must be a non-empty string")
        if self.base_url is not None and (not isinstance(self.base_url, str) or not self.base_url.strip()):
            raise ValueError("base_url must be a non-empty string or None")
        for name, value in (("provider_binding_id", self.provider_binding_id), ("credential_id", self.credential_id), ("api_key_env", self.api_key_env), ("project_id", self.project_id)):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a non-empty string or None")
        if self.intelligence_tier is not None:
            tier = self.intelligence_tier.value if isinstance(self.intelligence_tier, IntelligenceTier) else self.intelligence_tier
            if not isinstance(tier, str) or tier.strip() not in {item.value for item in IntelligenceTier}:
                raise ValueError("intelligence_tier must be one of L0, L1, L2, or L3")
            object.__setattr__(self, "intelligence_tier", tier.strip())
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, (int, float)) or not math.isfinite(float(self.timeout_seconds)) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        object.__setattr__(self, "provider_id", self.provider_id.strip())
        object.__setattr__(self, "model", self.model.strip())
        if self.base_url is not None:
            object.__setattr__(self, "base_url", self.base_url.strip())
        if self.provider_binding_id is not None:
            object.__setattr__(self, "provider_binding_id", self.provider_binding_id.strip())
        if self.credential_id is not None:
            object.__setattr__(self, "credential_id", self.credential_id.strip())
        if self.api_key_env is not None:
            object.__setattr__(self, "api_key_env", self.api_key_env.strip())
        if self.project_id is not None:
            object.__setattr__(self, "project_id", self.project_id.strip())
        # Endpoint and credential authority — must run after normalization so the
        # stripped values are checked.
        validate_endpoint_authority(self.provider_id, self.base_url)
        validate_api_key_env_authority(self.provider_id, self.api_key_env)

    @property
    def model_id(self) -> str:
        return self.model


class ProviderFactory:
    # SSOT shared with resources/provider_policy.py's instance-authority
    # validator -- see canonical_types.py.
    _PROVIDER_TYPES = CANONICAL_PROVIDER_MODULES

    def create(self, definition: ProviderDefinition) -> Any:
        if not isinstance(definition, ProviderDefinition):
            raise TypeError("definition must be a ProviderDefinition")
        provider_target = self._PROVIDER_TYPES.get(definition.provider_id)
        if provider_target is None:
            raise ValueError(f"unsupported provider: {definition.provider_id}")
        module_name, provider_name = provider_target
        provider_type = getattr(import_module(module_name, __package__), provider_name)
        arguments: dict[str, Any] = {
            "model": definition.model,
            "timeout_seconds": definition.timeout_seconds,
        }
        if definition.base_url is not None:
            arguments["base_url"] = definition.base_url
        provider = provider_type(**arguments)
        # These are non-secret identity labels.  Adapters continue to resolve
        # credentials only from their own external environment or secret store.
        setattr(provider, "provider_binding_id", definition.provider_binding_id or definition.provider_id)
        setattr(provider, "model_id", definition.model)
        setattr(provider, "credential_id", definition.credential_id)
        if definition.api_key_env is not None:
            setattr(provider, "api_key_env", definition.api_key_env)
        if definition.project_id is not None:
            setattr(provider, "project_id", definition.project_id)
        setattr(provider, "intelligence_tier", definition.intelligence_tier)
        return provider


__all__ = ["ProviderDefinition", "ProviderFactory"]
