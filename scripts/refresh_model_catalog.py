"""Refresh a candidate Provider model catalog from explicit discovery bindings.

This is an operator-invoked, read-only network operation.  It records only
provider/binding/model availability and timestamps: never credentials, raw API
responses, benchmark scores, capability claims, or routing admission.  The
result is still merely catalog evidence; alias, benchmark, capability,
qualification, billing, privacy, quota, and health policy remain independent
Host authorities.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dev_agent.providers.model_discovery import ModelDiscoveryBinding, ProviderModelDiscovery


def _bindings_from_document(document: Mapping[str, Any]) -> tuple[ModelDiscoveryBinding, ...]:
    raw_bindings = document.get("bindings")
    if not isinstance(raw_bindings, list) or not raw_bindings:
        raise ValueError("bindings document must contain a non-empty bindings array")
    bindings: list[ModelDiscoveryBinding] = []
    identities: set[tuple[str, str]] = set()
    for raw in raw_bindings:
        if not isinstance(raw, Mapping):
            raise ValueError("bindings entries must be objects")
        try:
            binding = ModelDiscoveryBinding(**dict(raw))
        except TypeError as exc:
            raise ValueError(f"invalid discovery binding: {exc}") from exc
        identity = (binding.provider_id, binding.provider_binding_id)
        if identity in identities:
            raise ValueError(f"duplicate discovery binding: {identity!r}")
        identities.add(identity)
        bindings.append(binding)
    return tuple(bindings)


def load_bindings(path: str | Path) -> tuple[ModelDiscoveryBinding, ...]:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("discovery bindings document is unreadable") from exc
    if not isinstance(document, Mapping) or document.get("schema_version") != 1:
        raise ValueError("discovery bindings document schema_version must be 1")
    return _bindings_from_document(document)


def refresh(bindings: tuple[ModelDiscoveryBinding, ...], *, discovery: ProviderModelDiscovery | None = None) -> dict[str, Any]:
    client = discovery or ProviderModelDiscovery()
    entries = []
    failures = []
    seen: set[tuple[str, str, str]] = set()
    for binding in bindings:
        try:
            result = client.discover(binding)
        except Exception as exc:
            # A discovery failure means this binding has no fresh availability
            # observation.  Keep successful independent bindings, and retain
            # only type-level diagnostics so a Provider response can never
            # become an outbound/credential artifact.
            failures.append(
                {
                    "provider_id": binding.provider_id,
                    "provider_binding_id": binding.provider_binding_id,
                    "category": type(exc).__name__,
                }
            )
            continue
        for entry in result.entries:
            identity = (entry.provider_id, entry.provider_binding_id, entry.model_id)
            if identity in seen:
                raise ValueError(f"duplicate discovered model identity: {identity!r}")
            seen.add(identity)
            entries.append(entry.to_dict())
    return {
        "schema_version": 1,
        "entries": sorted(entries, key=lambda entry: (entry["provider_id"], entry["provider_binding_id"], entry["model_id"])),
        "discovery_failures": failures,
    }


def write_candidate(path: str | Path, document: Mapping[str, Any]) -> None:
    """Atomically replace only the explicitly named operator output file."""
    target = Path(path).resolve()
    if target.parent == target or not target.name:
        raise ValueError("output path must be a file")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bindings", default=ROOT / "spec" / "v2" / "model_evidence" / "model_discovery_bindings.json")
    parser.add_argument("--output", required=True, help="explicit candidate snapshot path; no default canonical overwrite")
    args = parser.parse_args(argv)
    try:
        document = refresh(load_bindings(args.bindings))
        write_candidate(args.output, document)
    except Exception as exc:
        # Do not echo endpoint payloads or exception internals that might have
        # included an upstream diagnostic.  The binding config and candidate
        # file remain untouched on an unsuccessful refresh.
        print(json.dumps({"status": "failed", "category": type(exc).__name__}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "candidate_written", "entry_count": len(document["entries"]), "output": str(Path(args.output))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
