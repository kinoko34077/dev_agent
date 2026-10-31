"""Host-only composition of exact discovery, capability, and quality evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Mapping

from .model_benchmarks import BenchmarkCatalog
from .model_capabilities import ModelCapabilityCatalog
from .model_catalog import ModelAliasCatalog, ModelCatalog


# Static evidence diagnostics must not hide the difference between absent
# supplemental evidence and an explicit incompatible model. A current exact
# qualification can remain routable when the optional discovery snapshot is
# missing; a discovered specialized model or explicit missing text capability
# remains a hard interface denial.
STATIC_EVIDENCE_MISSING = "STATIC_EVIDENCE_MISSING"
STATIC_MODEL_UNSUPPORTED = "STATIC_MODEL_UNSUPPORTED"
STATIC_CAPABILITY_UNSUPPORTED = "STATIC_CAPABILITY_UNSUPPORTED"
QUALITY_ESTIMATED = "QUALITY_ESTIMATED"
QUALITY_UNVERIFIED = "QUALITY_UNVERIFIED"


@dataclass(frozen=True)
class ModelAdmission:
    """Advisory evidence for an exact model already admitted by Host authority.

    ``capabilities`` narrows a route only when a current exact capability row
    exists. ``benchmark_tier`` and ``task_fit`` are quality observations; they
    never grant production tier or turn into hard admission requirements.
    """

    canonical_model_id: str | None
    benchmark_tier: str | None
    intelligence_score: float | None
    task_fit: Mapping[str, float]
    capabilities: frozenset[str] | None
    quality_state: str

    def __post_init__(self) -> None:
        if self.benchmark_tier is not None and self.benchmark_tier not in {"L1", "L2", "L3"}:
            raise ValueError("benchmark_tier must be L1, L2, L3, or None")
        if self.quality_state not in {QUALITY_ESTIMATED, QUALITY_UNVERIFIED}:
            raise ValueError("quality_state must be estimated or unverified")
        if self.quality_state == QUALITY_UNVERIFIED and (self.benchmark_tier is not None or self.intelligence_score is not None or self.task_fit):
            raise ValueError("unverified quality cannot contain benchmark measurements")
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
    quality_state: str = QUALITY_UNVERIFIED

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
            "quality_state": self.quality_state,
        }


class ModelAdmissionResolver:
    """Compose optional model evidence without owning hard route authority.

    Qualification, billing, privacy, quota, health, and circuit state are not
    duplicated here. The caller must continue through the existing Router.
    Current discovery establishes the exact identity for this evidence view;
    benchmark absence is quality uncertainty, while capability absence yields
    no extra feature grant and leaves existing qualification/resource evidence
    as the only source for required capabilities.
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
        """Explain exact discovery/capability status and benchmark uncertainty."""

        discovered = self._catalog.lookup(provider_id, provider_binding_id, model_id, now=now)
        identity = (str(provider_id), str(provider_binding_id), str(model_id))
        if discovered is None:
            return ModelEvidenceDiagnostic(*identity, "MISSING", "-", "-", "-", STATIC_EVIDENCE_MISSING)
        if not discovered.is_text_generation_candidate():
            return ModelEvidenceDiagnostic(*identity, "SPECIALIZED", "-", "-", "-", STATIC_MODEL_UNSUPPORTED)
        alias = self._aliases.lookup(discovered.provider_id, discovered.model_id)
        capability = self._capabilities.lookup(discovered.provider_id, discovered.model_id, now=now)
        benchmark = self._benchmarks.lookup(alias.canonical_model_id, now=now) if alias is not None else None
        quality_state = QUALITY_ESTIMATED if benchmark is not None else QUALITY_UNVERIFIED
        if capability is not None and "text" not in capability.capabilities:
            return ModelEvidenceDiagnostic(
                *identity,
                "PASS",
                "PASS" if alias is not None else "MISSING",
                "PASS" if benchmark is not None else "MISSING",
                "INCOMPATIBLE",
                STATIC_CAPABILITY_UNSUPPORTED,
                alias.canonical_model_id if alias is not None else None,
                self._benchmarks.tier_for(benchmark) if benchmark is not None else None,
                quality_state,
            )
        return ModelEvidenceDiagnostic(
            *identity,
            "PASS",
            "PASS" if alias is not None else "MISSING",
            "PASS" if benchmark is not None else "MISSING",
            "PASS" if capability is not None else "MISSING",
            "ELIGIBLE",
            alias.canonical_model_id if alias is not None else None,
            self._benchmarks.tier_for(benchmark) if benchmark is not None else None,
            quality_state,
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
        if discovered is None:
            return None
        benchmark = (
            self._benchmarks.lookup(diagnostic.canonical_model_id, now=now)
            if diagnostic.canonical_model_id is not None
            else None
        )
        capability = self._capabilities.lookup(discovered.provider_id, discovered.model_id, now=now)
        return ModelAdmission(
            canonical_model_id=diagnostic.canonical_model_id,
            benchmark_tier=self._benchmarks.tier_for(benchmark) if benchmark is not None else None,
            intelligence_score=benchmark.normalized_score if benchmark is not None else None,
            task_fit=benchmark.task_fit if benchmark is not None else {},
            capabilities=capability.capabilities if capability is not None else None,
            quality_state=diagnostic.quality_state,
        )


__all__ = [
    "ModelAdmission",
    "ModelAdmissionResolver",
    "ModelEvidenceDiagnostic",
    "QUALITY_ESTIMATED",
    "QUALITY_UNVERIFIED",
    "STATIC_EVIDENCE_MISSING",
    "STATIC_CAPABILITY_UNSUPPORTED",
    "STATIC_MODEL_UNSUPPORTED",
]
