from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest

from src.dev_agent.security.egress import (
    EgressDecision,
    EgressFileEntry,
    EgressValidationError,
    StandingEgressGrant,
    attach_model_request_egress,
    build_egress_manifest,
)
from src.dev_agent.domain.protocol import ModelRequest
from src.dev_agent.security.protected_paths import PathProtectionClass


def _grant() -> StandingEgressGrant:
    return StandingEgressGrant(
        policy_id="low-risk-source-v1",
        destinations=("gemini", "cloudflare"),
        allowed_roots=("src", "tests", "docs"),
        max_sensitivity="normal",
    )


def test_clean_source_gets_allow_manifest_without_content() -> None:
    payload = b"def answer():\n    return 8\n"
    manifest = build_egress_manifest(
        task_id="task-1",
        destination="gemini",
        revision="revision-1",
        files=(("src/example.py", payload),),
        grant=_grant(),
    )

    assert manifest.decision is EgressDecision.ALLOW
    assert manifest.files[0].path == "src/example.py"
    assert manifest.files[0].sha256 == hashlib.sha256(payload).hexdigest()
    assert manifest.files[0].size_bytes == len(payload)
    assert manifest.files[0].secret_scan == "clean"
    assert manifest.files[0].path_class == PathProtectionClass.NORMAL_REPO.value
    serialized = manifest.to_dict()
    assert "content" not in str(serialized).lower()
    assert "return 8" not in str(serialized)
    assert manifest.manifest_sha256 == manifest.from_dict(serialized).manifest_sha256


def test_protected_path_is_denied_without_emitting_file_content() -> None:
    manifest = build_egress_manifest(
        task_id="task-1",
        destination="gemini",
        revision="revision-1",
        files=((".env.local", b"API_KEY=secret-value"),),
        grant=_grant(),
    )

    assert manifest.decision is EgressDecision.DENY
    assert "protected_path" in manifest.reasons
    assert manifest.files[0].path_class == PathProtectionClass.HARD_DENY.value
    assert "secret-value" not in str(manifest.to_dict())


def test_egress_file_entry_rejects_a_tampered_path_class() -> None:
    with pytest.raises(EgressValidationError, match="path_class"):
        EgressFileEntry(
            path="src/example.py",
            sha256=hashlib.sha256(b"pass\n").hexdigest(),
            size_bytes=5,
            path_class=PathProtectionClass.HARD_DENY.value,
        )


@pytest.mark.parametrize("path", ["C:/outside.py", r"C:\outside.py", "C:outside.py"])
def test_egress_file_entry_rejects_windows_drive_paths(path: str) -> None:
    with pytest.raises(EgressValidationError, match="relative path"):
        EgressFileEntry(
            path=path,
            sha256=hashlib.sha256(b"pass\n").hexdigest(),
            size_bytes=5,
        )


def test_secret_content_is_denied_by_host_scan() -> None:
    manifest = build_egress_manifest(
        task_id="task-1",
        destination="gemini",
        revision="revision-1",
        files=(("src/example.py", b"api_key = 'AIza12345678901234567890'\n"),),
        grant=_grant(),
    )

    assert manifest.decision is EgressDecision.DENY
    assert "secret_detected" in manifest.reasons
    assert "AIza12345678901234567890" not in str(manifest.to_dict())


def test_unknown_text_extension_requires_review() -> None:
    manifest = build_egress_manifest(
        task_id="task-1",
        destination="gemini",
        revision="revision-1",
        files=(("src/example.custom", b"plain text\n"),),
        grant=_grant(),
    )

    assert manifest.decision is EgressDecision.REVIEW
    assert "unknown_extension" in manifest.reasons


def test_destination_and_size_are_standing_grant_boundaries() -> None:
    with pytest.raises(EgressValidationError, match="destination"):
        build_egress_manifest(
            task_id="task-1",
            destination="openrouter",
            revision="revision-1",
            files=(("src/example.py", b"pass\n"),),
            grant=_grant(),
        )

    small_grant = StandingEgressGrant(
        policy_id="small-v1",
        destinations=("gemini",),
        allowed_roots=("src",),
        max_bytes=4,
    )
    manifest = build_egress_manifest(
        task_id="task-1",
        destination="gemini",
        revision="revision-1",
        files=(("src/example.py", b"pass\n"),),
        grant=small_grant,
    )
    assert manifest.decision is EgressDecision.DENY
    assert "size_limit" in manifest.reasons


def test_invalid_grant_does_not_create_a_broad_default() -> None:
    with pytest.raises(EgressValidationError):
        StandingEgressGrant(
            policy_id="bad",
            destinations=("gemini",),
            allowed_roots=(),
        )


def test_model_request_egress_attaches_digest_without_retaining_prompt_content() -> None:
    request = ModelRequest(
        task_id=str(uuid4()),
        messages=[{"role": "user", "content": "bounded planning request"}],
        metadata={"planning_mode": "proposal_only"},
    )

    prepared, manifest = attach_model_request_egress(request)

    assert prepared.metadata["egress_manifest_sha256"] == manifest.manifest_sha256
    assert prepared.metadata["egress_manifest_policy"] == "dev-agent-model-request-egress-v1"
    assert manifest.decision is EgressDecision.ALLOW
    serialized = manifest.to_dict()
    assert "bounded planning request" not in str(serialized)
    assert "content" not in str(serialized).lower()


def test_model_request_egress_rejects_secret_shaped_prompt_content() -> None:
    request = ModelRequest(
        task_id=str(uuid4()),
        messages=[{"role": "user", "content": "api_key=super-secret-value"}],
    )

    with pytest.raises(EgressValidationError, match="secret candidate"):
        attach_model_request_egress(request)
