"""Explicit runtime composition for independent model evidence snapshots."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from .model_admission import ModelAdmissionResolver
from .model_benchmarks import BenchmarkCatalog
from .model_capabilities import ModelCapabilityCatalog
from .model_catalog import ModelAliasCatalog, ModelCatalog, ModelCatalogError


_FILENAMES = {
    "catalog": "model_catalog_snapshot.json",
    "aliases": "model_alias_catalog.json",
    "benchmarks": "model_benchmark_snapshot.json",
    "capabilities": "model_capability_snapshot.json",
}
DEFAULT_MODEL_EVIDENCE_DIRECTORY = Path(__file__).resolve().parents[3] / "spec" / "v2" / "model_evidence"


def _load_document(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ModelCatalogError(f"model evidence snapshot is missing: {path.name}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelCatalogError(f"model evidence snapshot is unreadable: {path.name}") from exc
    if not isinstance(value, Mapping):
        raise ModelCatalogError(f"model evidence snapshot must be an object: {path.name}")
    return value


@dataclass(frozen=True)
class ModelEvidenceCatalog:
    """Four independent static evidence layers, loaded only by composition."""

    catalog: ModelCatalog
    aliases: ModelAliasCatalog
    benchmarks: BenchmarkCatalog
    capabilities: ModelCapabilityCatalog

    @property
    def resolver(self) -> ModelAdmissionResolver:
        return ModelAdmissionResolver(self.catalog, self.aliases, self.benchmarks, self.capabilities)

    @classmethod
    def load(cls, directory: str | Path) -> "ModelEvidenceCatalog":
        root = Path(directory)
        documents = {name: _load_document(root / filename) for name, filename in _FILENAMES.items()}
        return cls(
            catalog=ModelCatalog.from_document(documents["catalog"]),
            aliases=ModelAliasCatalog.from_document(documents["aliases"]),
            benchmarks=BenchmarkCatalog.from_document(documents["benchmarks"]),
            capabilities=ModelCapabilityCatalog.from_document(documents["capabilities"]),
        )

    @classmethod
    def load_default(cls) -> "ModelEvidenceCatalog":
        """Load the reviewed repository snapshot only during explicit composition."""
        return cls.load(DEFAULT_MODEL_EVIDENCE_DIRECTORY)


__all__ = ["DEFAULT_MODEL_EVIDENCE_DIRECTORY", "ModelEvidenceCatalog"]
