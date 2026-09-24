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
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dev_agent.providers.model_discovery import ModelDiscoveryBinding, ProviderModelDiscovery
from src.dev_agent.providers.base import TransportStage, project_transport_failure


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


def refresh(
    bindings: tuple[ModelDiscoveryBinding, ...],
    *,
    discovery: ProviderModelDiscovery | None = None,
    execution_boundary: str | None = None,
) -> dict[str, Any]:
    client = discovery or ProviderModelDiscovery()
    entries = []
    failures = []
    refreshed_bindings: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for binding in bindings:
        try:
            result = client.discover(binding)
        except Exception as exc:
            # A discovery failure means this binding has no fresh availability
            # observation.  Keep successful independent bindings, and retain
            # only type-level diagnostics so a Provider response can never
            # become an outbound/credential artifact.
            failure = {
                "provider_id": binding.provider_id,
                "provider_binding_id": binding.provider_binding_id,
                "category": type(exc).__name__,
            }
            if execution_boundary is not None:
                diagnostics = project_transport_failure(
                    exc,
                    execution_boundary=execution_boundary,
                    stage=TransportStage.RESPONSE_WAIT,
                )
                failure.update({key: value for key, value in diagnostics.items() if value is not None})
            failures.append(failure)
            continue
        refreshed_bindings.append(
            {
                "provider_id": binding.provider_id,
                "provider_binding_id": binding.provider_binding_id,
            }
        )
        for entry in result.entries:
            identity = (entry.provider_id, entry.provider_binding_id, entry.model_id)
            if identity in seen:
                raise ValueError(f"duplicate discovered model identity: {identity!r}")
            seen.add(identity)
            entries.append(entry.to_dict())
    return {
        "schema_version": 1,
        "entries": sorted(entries, key=lambda entry: (entry["provider_id"], entry["provider_binding_id"], entry["model_id"])),
        "refreshed_bindings": sorted(refreshed_bindings, key=lambda item: (item["provider_id"], item["provider_binding_id"])),
        "discovery_failures": failures,
    }


def merge_catalog_documents(base: Mapping[str, Any], refreshed: Mapping[str, Any]) -> dict[str, Any]:
    """Merge successful binding refreshes without dropping unrelated snapshots."""

    if base.get("schema_version") != 1 or refreshed.get("schema_version") != 1:
        raise ValueError("model catalog documents must use schema_version 1")
    base_entries = base.get("entries")
    refreshed_entries = refreshed.get("entries")
    if not isinstance(base_entries, list) or not isinstance(refreshed_entries, list):
        raise ValueError("model catalog documents must contain entries arrays")
    raw_bindings = refreshed.get("refreshed_bindings")
    if isinstance(raw_bindings, list):
        refreshed_bindings = {
            (item.get("provider_id"), item.get("provider_binding_id"))
            for item in raw_bindings
            if isinstance(item, Mapping)
        }
    else:
        refreshed_bindings = {
            (item.get("provider_id"), item.get("provider_binding_id"))
            for item in refreshed_entries
            if isinstance(item, Mapping)
        }
    merged: dict[tuple[Any, Any, Any], Mapping[str, Any]] = {}
    for entry in base_entries:
        if not isinstance(entry, Mapping):
            raise ValueError("base catalog entries must be objects")
        identity = (entry.get("provider_id"), entry.get("provider_binding_id"), entry.get("model_id"))
        if identity[:2] not in refreshed_bindings:
            merged[identity] = dict(entry)
    for entry in refreshed_entries:
        if not isinstance(entry, Mapping):
            raise ValueError("refreshed catalog entries must be objects")
        identity = (entry.get("provider_id"), entry.get("provider_binding_id"), entry.get("model_id"))
        if identity in merged:
            raise ValueError(f"duplicate merged model identity: {identity!r}")
        merged[identity] = dict(entry)
    return {
        "schema_version": 1,
        "entries": [merged[key] for key in sorted(merged, key=lambda item: tuple(str(value) for value in item))],
        "refreshed_bindings": sorted(
            [
                {"provider_id": provider, "provider_binding_id": binding}
                for provider, binding in refreshed_bindings
                if isinstance(provider, str) and isinstance(binding, str)
            ],
            key=lambda item: (item["provider_id"], item["provider_binding_id"]),
        ),
        "discovery_failures": list(refreshed.get("discovery_failures", [])) if isinstance(refreshed.get("discovery_failures", []), list) else [],
    }


def write_candidate(
    path: str | Path,
    document: Mapping[str, Any],
    *,
    replace_existing: bool = False,
) -> None:
    """Write an explicitly named candidate without implicit snapshot replacement."""
    target = Path(path).resolve()
    if target.parent == target or not target.name:
        raise ValueError("output path must be a file")
    if target.exists() and not replace_existing:
        raise FileExistsError("candidate output already exists; pass replace_existing explicitly")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.replace(target)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bindings", default=ROOT / "spec" / "v2" / "model_evidence" / "model_discovery_bindings.json")
    parser.add_argument("--output", required=True, help="explicit candidate snapshot path; no default canonical overwrite")
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="explicitly replace an existing candidate path; omitted paths are create-only",
    )
    parser.add_argument(
        "--merge-into",
        type=Path,
        help="merge successful refreshed bindings into an existing catalog before writing the candidate",
    )
    parser.add_argument(
        "--execution-boundary",
        choices=("codex_sandbox", "host_process", "provider_process"),
        help="explicit boundary for bounded transport diagnostics; does not change routing or network policy",
    )
    args = parser.parse_args(argv)
    try:
        document = refresh(load_bindings(args.bindings), execution_boundary=args.execution_boundary)
        if args.merge_into is not None:
            base = json.loads(args.merge_into.read_text(encoding="utf-8"))
            if not isinstance(base, Mapping):
                raise ValueError("merge target must be a model catalog object")
            document = merge_catalog_documents(base, document)
        write_candidate(args.output, document, replace_existing=args.replace_existing)
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
