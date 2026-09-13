"""Host-only composition of model discovery, benchmark, and capability facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Mapping

from .model_benchmarks import BenchmarkCatalog
from .model_capabilities import ModelCapabilityCatalog
from .model_catalog import ModelAliasCatalog, ModelCatalog


@dataclass(frozen=True)
class ModelAdmission:
    """Non-authoritative model evidence ready for existing Router hard filters."""

    canonical_model_id: str
    intelligence_tier: str
    intelligence_score: float
    task_fit: Mapping[str, float]
    capabilities: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_fit", MappingProxyType(dict(self.task_fit)))


@dataclass(frozen=True)
class ModelEvidenceDiagnostic:
    """Bounded, non-secret explanation of the static admission gates."""

    provider_id: str
    provider_binding_id: str
    model_id: str
    discovery: str
    alias: str
    benchmark: str
    capability: str
    result: str
    canonical_model_id: str | None = None
    intelligence_tier: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "provider_id": self.provider_id,
            "provider_binding_id": self.provider_binding_id,
            "model_id": self.model_id,
            "discovery": self.discovery,
            "alias": self.alias,
            "benchmark": self.benchmark,
            "capability": self.capability,
            "result": self.result,
            "canonical_model_id": self.canonical_model_id,
            "intelligence_tier": self.intelligence_tier,
        }


class ModelAdmissionResolver:
    """Require all three static evidence layers before returning a model fact.

    Qualification, billing, privacy, quota, health, and circuit state are not
    duplicated here.  The caller must continue through the existing Router.
    """

    def __init__(
        self,
        catalog: ModelCatalog,
        aliases: ModelAliasCatalog,
        benchmarks: BenchmarkCatalog,
        capabilities: ModelCapabilityCatalog,
    ) -> None:
        if not isinstance(catalog, ModelCatalog):
            raise TypeError("catalog must be a ModelCatalog")
        if not isinstance(benchmarks, BenchmarkCatalog):
            raise TypeError("benchmarks must be a BenchmarkCatalog")
        if not isinstance(aliases, ModelAliasCatalog):
            raise TypeError("aliases must be a ModelAliasCatalog")
        if not isinstance(capabilities, ModelCapabilityCatalog):
            raise TypeError("capabilities must be a ModelCapabilityCatalog")
        self._catalog = catalog
        self._aliases = aliases
        self._benchmarks = benchmarks
        self._capabilities = capabilities

    @property
    def catalog(self) -> ModelCatalog:
        return self._catalog

    @property
    def aliases(self) -> ModelAliasCatalog:
        return self._aliases

    @property
    def benchmarks(self) -> BenchmarkCatalog:
        return self._benchmarks

    @property
    def capabilities(self) -> ModelCapabilityCatalog:
        return self._capabilities

    def diagnose(
        self,
        provider_id: str,
        provider_binding_id: str,
        model_id: str,
        *,
        now: datetime | None = None,
    ) -> ModelEvidenceDiagnostic:
        """Explain the first missing static evidence gate without raw payloads."""

        discovered = self._catalog.lookup(provider_id, provider_binding_id, model_id, now=now)
        identity = (str(provider_id), str(provider_binding_id), str(model_id))
        if discovered is None:
            return ModelEvidenceDiagnostic(*identity, "MISSING", "-", "-", "-", "RUNTIME_UNAVAILABLE")
        if not discovered.is_text_generation_candidate():
            return ModelEvidenceDiagnostic(*identity, "SPECIALIZED", "-", "-", "-", "RUNTIME_UNAVAILABLE")
        alias = self._aliases.lookup(discovered.provider_id, discovered.model_id)
        if alias is None:
            return ModelEvidenceDiagnostic(*identity, "PASS", "MISSING", "-", "-", "ALIAS_MISSING")
        benchmark = self._benchmarks.lookup(alias.canonical_model_id, now=now)
        if benchmark is None:
            return ModelEvidenceDiagnostic(*identity, "PASS", "PASS", "MISSING", "-", "BENCHMARK_MISSING", alias.canonical_model_id)
        capability = self._capabilities.lookup(discovered.provider_id, discovered.model_id, now=now)
        if capability is None:
            return ModelEvidenceDiagnostic(*identity, "PASS", "PASS", "PASS", "MISSING", "CAPABILITY_MISSING", alias.canonical_model_id, self._benchmarks.tier_for(benchmark))
        return ModelEvidenceDiagnostic(
            *identity,
            "PASS",
            "PASS",
            "PASS",
            "PASS",
            "ELIGIBLE",
            alias.canonical_model_id,
            self._benchmarks.tier_for(benchmark),
        )

    def resolve(
        self,
        provider_id: str,
        provider_binding_id: str,
        model_id: str,
        *,
        now: datetime | None = None,
    ) -> ModelAdmission | None:
        diagnostic = self.diagnose(provider_id, provider_binding_id, model_id, now=now)
        if diagnostic.result != "ELIGIBLE":
            return None
        discovered = self._catalog.lookup(provider_id, provider_binding_id, model_id, now=now)
        if discovered is None or diagnostic.canonical_model_id is None:
            return None
        benchmark = self._benchmarks.lookup(diagnostic.canonical_model_id, now=now)
        capability = self._capabilities.lookup(discovered.provider_id, discovered.model_id, now=now)
        if benchmark is None or capability is None:
            return None
        return ModelAdmission(
            canonical_model_id=diagnostic.canonical_model_id,
            intelligence_tier=self._benchmarks.tier_for(benchmark),
            intelligence_score=benchmark.normalized_score,
            task_fit=benchmark.task_fit,
            capabilities=capability.capabilities,
        )


__all__ = ["ModelAdmission", "ModelAdmissionResolver", "ModelEvidenceDiagnostic"]
