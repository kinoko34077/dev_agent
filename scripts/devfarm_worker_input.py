"""Host-owned Worker input and Egress Manifest composition.

The caller supplies the already validated manifest and an exact Git-object
reader.  This boundary performs bounded decoding and standing-policy checks;
it does not open files, contact a Provider, or decide Task ownership.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from scripts.devfarm_contracts import MAX_OUTBOUND_BYTES, MAX_OUTBOUND_FILES
from scripts.devfarm_errors import DevFarmError
from src.dev_agent.security.egress import (
    EgressDecision,
    EgressManifest,
    StandingEgressGrant,
    build_egress_manifest,
)


MAX_INPUT_FILE_BYTES = 64 * 1024
GitObjectReader = Callable[[str, str], bytes]


def build_worker_input_context(
    manifest: Mapping[str, Any],
    *,
    read_file_at_revision: GitObjectReader,
    destination: str | None = None,
) -> tuple[str, EgressManifest]:
    """Build bounded file context and an ALLOW/REVIEW/DENY Egress Manifest."""

    if not isinstance(manifest, Mapping):
        raise TypeError("manifest must be a mapping")
    if not callable(read_file_at_revision):
        raise TypeError("read_file_at_revision must be callable")
    outbound_files = manifest.get("outbound_files")
    approved_provider_ids = manifest.get("approved_provider_ids")
    if not isinstance(outbound_files, list):
        raise DevFarmError("worker outbound_files must be a list")
    if not isinstance(approved_provider_ids, list) or not approved_provider_ids:
        raise DevFarmError("worker approved_provider_ids must be a non-empty list")

    chunks: list[str] = []
    files: list[tuple[str, bytes]] = []
    aggregate_bytes = 0
    for relative in outbound_files:
        if not isinstance(relative, str) or not relative.strip():
            raise DevFarmError("worker outbound file path must be a non-empty string")
        normalized = relative.strip()
        try:
            data = read_file_at_revision(str(manifest["base_revision"]), normalized)
        except DevFarmError as exc:
            raise DevFarmError(f"worker input file cannot be read: {normalized}: {exc}") from exc
        if not isinstance(data, bytes):
            raise DevFarmError(f"worker input reader must return bytes: {normalized}")
        if len(data) > MAX_INPUT_FILE_BYTES:
            raise DevFarmError(f"worker input file exceeds {MAX_INPUT_FILE_BYTES} bytes: {normalized}")
        aggregate_bytes += len(data)
        if aggregate_bytes > MAX_OUTBOUND_BYTES:
            raise DevFarmError(f"worker outbound files exceed {MAX_OUTBOUND_BYTES} aggregate bytes")
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DevFarmError(f"worker input file is not UTF-8: {normalized}") from exc
        files.append((normalized, data))
        chunks.append(f"\n--- BEGIN FILE {normalized} ---\n{content}\n--- END FILE {normalized} ---\n")

    selected_destination = destination or approved_provider_ids[0]
    grant = StandingEgressGrant(
        policy_id="devfarm-low-risk-source-egress-v1",
        destinations=tuple(approved_provider_ids),
        allowed_roots=tuple(outbound_files) or ("__no_outbound_files__",),
        max_files=MAX_OUTBOUND_FILES,
        max_bytes=MAX_OUTBOUND_BYTES,
        max_file_bytes=MAX_INPUT_FILE_BYTES,
    )
    egress_manifest = build_egress_manifest(
        task_id=str(manifest["task_id"]),
        destination=selected_destination,
        revision=str(manifest["base_revision"]),
        files=tuple(files),
        grant=grant,
    )
    if egress_manifest.decision is not EgressDecision.ALLOW:
        reasons = ", ".join(egress_manifest.reasons) or "host_policy_rejected"
        if "secret_detected" in egress_manifest.reasons:
            raise DevFarmError("worker outbound source contains a secret candidate")
        raise DevFarmError(f"worker egress preflight did not allow outbound source: {reasons}")
    return "".join(chunks), egress_manifest


__all__ = ["MAX_INPUT_FILE_BYTES", "build_worker_input_context"]
