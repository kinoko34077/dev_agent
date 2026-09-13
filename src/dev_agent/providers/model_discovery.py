"""Read-only Provider model discovery with no routing authority.

This module knows the documented *list models* response shapes for supported
Providers.  It does not construct Providers, update a trusted catalog, or
make a discovered model dispatchable.  The Host must validate and promote a
candidate snapshot through the separate model-admission path.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import os
import re
from types import MappingProxyType
from typing import Any
from urllib.request import Request

from .openai_compatible.http import _read_bounded, urlopen_no_redirect


_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_DEFAULT_TTL = timedelta(days=7)
_MAX_RESPONSE_BYTES = 1_048_576
_MAX_METADATA_STRING = 256
_MAX_METADATA_ITEMS = 32


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _env_name(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    candidate = _text(value, name)
    if not _ENV_NAME.fullmatch(candidate):
        raise ValueError(f"{name} must be an uppercase environment variable name")
    return candidate


def _utc_now(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must include a timezone")
    return current.astimezone(timezone.utc)


def _bounded_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > _MAX_METADATA_STRING:
        return None
    return value


def _bounded_string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list) or len(value) > _MAX_METADATA_ITEMS:
        return None
    values = [_bounded_string(item) for item in value]
    if any(item is None for item in values):
        return None
    return list(dict.fromkeys(values))  # type: ignore[arg-type]


def _bounded_positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > 10_000_000_000:
        return None
    return value


def _metadata_for(provider_id: str, raw_model: Mapping[str, Any]) -> Mapping[str, Any]:
    """Keep only bounded, stable model-list facts useful to later admission.

    Discovery is deliberately not a routing authority.  The allowlist here
    prevents descriptions, pricing, arbitrary provider payload, and secrets
    from becoming part of a durable snapshot while retaining enough metadata
    to distinguish ordinary text models from specialized endpoints.
    """

    metadata: dict[str, Any] = {}
    if provider_id == "gemini":
        string_fields = {
            "display_name": "displayName",
            "version": "version",
            "base_model_id": "baseModelId",
        }
        for output_name, input_name in string_fields.items():
            value = _bounded_string(raw_model.get(input_name))
            if value is not None:
                metadata[output_name] = value
        methods = _bounded_string_list(raw_model.get("supportedGenerationMethods"))
        if methods is not None:
            metadata["supported_generation_methods"] = methods
        for output_name, input_name in (
            ("input_token_limit", "inputTokenLimit"),
            ("output_token_limit", "outputTokenLimit"),
        ):
            value = _bounded_positive_int(raw_model.get(input_name))
            if value is not None:
                metadata[output_name] = value
        if isinstance(raw_model.get("thinking"), bool):
            metadata["thinking_supported"] = raw_model["thinking"]
        if isinstance(raw_model.get("deprecated"), bool):
            metadata["deprecated"] = raw_model["deprecated"]
    elif provider_id == "openrouter":
        context_length = _bounded_positive_int(raw_model.get("context_length"))
        if context_length is not None:
            metadata["context_length"] = context_length
        architecture = raw_model.get("architecture")
        architecture = architecture if isinstance(architecture, Mapping) else {}
        modality = _bounded_string(architecture.get("modality"))
        if modality is not None:
            metadata["modality"] = modality
        for output_name, input_name in (
            ("input_modalities", "input_modalities"),
            ("output_modalities", "output_modalities"),
            ("supported_parameters", "supported_parameters"),
        ):
            values = _bounded_string_list(raw_model.get(input_name))
            if values is None and input_name in {"input_modalities", "output_modalities"}:
                values = _bounded_string_list(architecture.get(input_name))
            if values is not None:
                metadata[output_name] = values
    return MappingProxyType(metadata)


@dataclass(frozen=True)
class ModelDiscoveryBinding:
    """Identity and credential references for one read-only discovery call."""

    provider_id: str
    provider_binding_id: str
    api_key_env: str | None = None
    account_id_env: str | None = None
    timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _text(self.provider_id, "provider_id"))
        object.__setattr__(self, "provider_binding_id", _text(self.provider_binding_id, "provider_binding_id"))
        object.__setattr__(self, "api_key_env", _env_name(self.api_key_env, "api_key_env"))
        object.__setattr__(self, "account_id_env", _env_name(self.account_id_env, "account_id_env"))
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, (int, float)):
            raise ValueError("timeout_seconds must be numeric")
        if not 0 < float(self.timeout_seconds) <= 120:
            raise ValueError("timeout_seconds must be from 0 to 120")
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))


@dataclass(frozen=True)
class DiscoveredModel:
    provider_id: str
    provider_binding_id: str
    model_id: str
    source: str
    observed_at: str
    expires_at: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, Mapping):
            raise ValueError("metadata must be an object")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "provider_id": self.provider_id,
            "provider_binding_id": self.provider_binding_id,
            "model_id": self.model_id,
            "source": self.source,
            "observed_at": self.observed_at,
            "expires_at": self.expires_at,
        }
        if self.metadata:
            document["metadata"] = dict(self.metadata)
        return document


@dataclass(frozen=True)
class ModelDiscoveryResult:
    """Candidate-only discovery evidence, safe to serialize without secrets."""

    entries: tuple[DiscoveredModel, ...]

    def to_document(self) -> dict[str, Any]:
        return {"schema_version": 1, "entries": [entry.to_dict() for entry in self.entries]}


HttpGet = Callable[[str, Mapping[str, str], float], Mapping[str, Any]]


class ProviderModelDiscovery:
    """Calls fixed official endpoints and normalizes their model-list shapes.

    ``secret_getter`` is injected primarily for tests.  It is used only to
    create the outbound header and no secret value is persisted or returned.
    """

    _SUPPORTED = frozenset(
        {
            "gemini",
            "cloudflare",
            "openrouter",
            "groq",
            "mistral",
            "sambanova",
            "ollama",
            "ollama_cloud",
            "vercel",
        }
    )
    _UNAUTHENTICATED = frozenset({"ollama", "vercel"})

    def __init__(self, *, secret_getter: Callable[[str], str | None] = os.getenv, ttl: timedelta = _DEFAULT_TTL) -> None:
        if not isinstance(ttl, timedelta) or ttl <= timedelta(0) or ttl > timedelta(days=31):
            raise ValueError("ttl must be from one second to 31 days")
        self._secret_getter = secret_getter
        self._ttl = ttl

    def discover(
        self,
        binding: ModelDiscoveryBinding,
        *,
        now: datetime | None = None,
        http_get: HttpGet | None = None,
    ) -> ModelDiscoveryResult:
        if binding.provider_id not in self._SUPPORTED:
            raise ValueError(f"unsupported provider for model discovery: {binding.provider_id}")
        current = _utc_now(now)
        url, headers = self._request_for(binding)
        document = (http_get or self._get_json)(url, headers, binding.timeout_seconds)
        model_records = self._model_records(binding.provider_id, document)
        observed_at = current.isoformat()
        expires_at = (current + self._ttl).isoformat()
        return ModelDiscoveryResult(
            tuple(
                DiscoveredModel(
                    provider_id=binding.provider_id,
                    provider_binding_id=binding.provider_binding_id,
                    model_id=model_id,
                    source=f"{binding.provider_id}.models.list",
                    observed_at=observed_at,
                    expires_at=expires_at,
                    metadata=metadata,
                )
                for model_id, metadata in model_records
            )
        )

    def _request_for(self, binding: ModelDiscoveryBinding) -> tuple[str, Mapping[str, str]]:
        provider = binding.provider_id
        headers: dict[str, str] = {"Accept": "application/json"}
        if provider == "gemini":
            headers["X-Goog-Api-Key"] = self._required_secret(binding.api_key_env, "api_key_env")
            return "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000", headers
        if provider == "cloudflare":
            account_id = self._required_secret(binding.account_id_env, "account_id_env")
            headers["Authorization"] = f"Bearer {self._required_secret(binding.api_key_env, 'api_key_env')}"
            return f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/models/search", headers
        if provider == "openrouter":
            headers["Authorization"] = f"Bearer {self._required_secret(binding.api_key_env, 'api_key_env')}"
            return "https://openrouter.ai/api/v1/models", headers
        if provider == "groq":
            headers["Authorization"] = f"Bearer {self._required_secret(binding.api_key_env, 'api_key_env')}"
            return "https://api.groq.com/openai/v1/models", headers
        if provider == "mistral":
            headers["Authorization"] = f"Bearer {self._required_secret(binding.api_key_env, 'api_key_env')}"
            return "https://api.mistral.ai/v1/models", headers
        if provider == "sambanova":
            headers["Authorization"] = f"Bearer {self._required_secret(binding.api_key_env, 'api_key_env')}"
            return "https://api.sambanova.ai/v1/models", headers
        if provider == "ollama":
            return "http://127.0.0.1:11434/api/tags", headers
        if provider == "ollama_cloud":
            headers["Authorization"] = f"Bearer {self._required_secret(binding.api_key_env, 'api_key_env')}"
            return "https://ollama.com/api/tags", headers
        if provider == "vercel":
            return "https://ai-gateway.vercel.sh/v1/models", headers
        raise AssertionError("supported provider dispatch is incomplete")

    def _required_secret(self, environment_name: str | None, field: str) -> str:
        if environment_name is None:
            raise ValueError(f"{field} is required for authenticated model discovery")
        value = self._secret_getter(environment_name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field} is not configured")
        return value

    @staticmethod
    def _get_json(url: str, headers: Mapping[str, str], timeout_seconds: float) -> Mapping[str, Any]:
        request = Request(url, headers=dict(headers), method="GET")
        with urlopen_no_redirect(request, timeout=timeout_seconds) as response:
            raw = _read_bounded(response, _MAX_RESPONSE_BYTES)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("model list response is not valid JSON") from exc
        if not isinstance(parsed, Mapping):
            raise ValueError("model list response must be an object")
        return parsed

    @classmethod
    def _model_ids(cls, provider_id: str, document: Mapping[str, Any]) -> tuple[str, ...]:
        return tuple(model_id for model_id, _metadata in cls._model_records(provider_id, document))

    @classmethod
    def _model_records(cls, provider_id: str, document: Mapping[str, Any]) -> tuple[tuple[str, Mapping[str, Any]], ...]:
        if not isinstance(document, Mapping):
            raise ValueError("model list response must be an object")
        if provider_id in {"gemini", "ollama", "ollama_cloud"}:
            raw_models = document.get("models")
        elif provider_id == "cloudflare":
            raw_models = document.get("result")
        else:
            raw_models = document.get("data")
        if not isinstance(raw_models, list) or not raw_models:
            raise ValueError("model list response must contain a non-empty model list")
        records: list[tuple[str, Mapping[str, Any]]] = []
        for raw_model in raw_models:
            if not isinstance(raw_model, Mapping):
                raise ValueError("model list contains an invalid model entry")
            if provider_id == "gemini":
                identifier = raw_model.get("name")
                if isinstance(identifier, str) and identifier.startswith("models/"):
                    identifier = identifier.removeprefix("models/")
            elif provider_id in {"ollama", "ollama_cloud"}:
                identifier = raw_model.get("model", raw_model.get("name"))
            else:
                identifier = raw_model.get("id", raw_model.get("name"))
            if not isinstance(identifier, str) or not identifier.strip():
                raise ValueError("model list contains an invalid model identifier")
            normalized_identifier = identifier.strip()
            records.append((normalized_identifier, _metadata_for(provider_id, raw_model)))
        identifiers = [identifier for identifier, _metadata in records]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("model list contains duplicate model identifiers")
        return tuple(records)


__all__ = [
    "DiscoveredModel",
    "ModelDiscoveryBinding",
    "ModelDiscoveryResult",
    "ProviderModelDiscovery",
]
