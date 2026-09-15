from __future__ import annotations

from scripts.devfarm_worker_prompt import build_worker_prompt


def test_worker_prompt_is_bounded_to_manifest_and_host_egress_projection() -> None:
    manifest = {
        "task_id": "task-1",
        "task_type": "implementation",
        "objective": "make a narrow change",
        "base_revision": "abc123",
        "allowed_files": ["src/example.py"],
        "forbidden_files": [".env"],
        "external_provider_allowed": True,
        "approved_provider_ids": ["gemini"],
        "outbound_files": ["src/example.py"],
        "requirements": ["keep the public contract"],
        "acceptance": ["focused test passes"],
        "test_commands": ["python -m pytest tests/v2/test_example.py -q"],
        "output_contract": {"format": "json"},
    }
    egress = {
        "policy_id": "devfarm-low-risk-source-egress-v1",
        "destination": "gemini",
        "manifest_sha256": "a" * 64,
        "decision": "ALLOW",
    }

    prompt = build_worker_prompt(manifest, "--- BEGIN FILE src/example.py ---\npass\n--- END FILE ---", egress_manifest=egress)

    assert '"task_id": "task-1"' in prompt
    assert '"manifest_sha256": "' + ("a" * 64) + '"' in prompt
    assert "Do not request credentials" in prompt
    assert "diff --git" in prompt
    assert "escape every newline as \\n" in prompt
    assert "never use `/dev/null` for an existing listed file" in prompt
    assert "For an existing file, copy this exact diff shape" in prompt
    assert "file_replacements" in prompt
    assert "The host will create the unified diff" in prompt
