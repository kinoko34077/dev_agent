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


def test_local_ollama_prompt_uses_only_the_complete_replacement_contract() -> None:
    manifest = {
        "task_id": "task-local-1",
        "task_type": "implementation",
        "objective": "format one existing module without changing behavior",
        "base_revision": "abc123",
        "allowed_files": ["src/example.py"],
        "forbidden_files": [".env"],
        "external_provider_allowed": True,
        "approved_provider_ids": ["ollama"],
        "outbound_files": ["src/example.py"],
        "requirements": ["preserve behavior"],
        "acceptance": ["focused test passes"],
        "test_commands": ["python -m compileall -q src/example.py"],
        "output_contract": {"format": "json"},
    }

    prompt = build_worker_prompt(
        manifest,
        "--- BEGIN FILE src/example.py ---\nvalue = 1\n--- END FILE ---",
        local_ollama=True,
    )

    assert "file_replacements is REQUIRED" in prompt
    assert "patch MUST be an empty string" in prompt
    assert "complete line 1" in prompt
    assert "The host joins" in prompt
    assert "adds one final newline" in prompt
    assert "MUST NOT contain a newline character" in prompt
    assert "known_issues, and assumptions MUST all be JSON arrays" in prompt
    assert "Do not use files, diff, reason, or commit_message" in prompt
    assert "The host will validate the replacement" in prompt


def test_rework_prompt_puts_concrete_repair_directive_before_general_contract() -> None:
    manifest = {
        "task_id": "task-rework-1",
        "task_type": "implementation",
        "objective": "make a narrow change",
        "base_revision": "abc123",
        "allowed_files": ["src/example.py"],
        "forbidden_files": [".env"],
        "external_provider_allowed": True,
        "approved_provider_ids": ["ollama"],
        "outbound_files": ["src/example.py"],
        "requirements": ["preserve behavior"],
        "acceptance": ["focused test passes"],
        "test_commands": ["python -m pytest tests/v2/test_example.py -q"],
        "output_contract": {"format": "json"},
        "rework_handoff": {
            "payload_reference": {
                "repair_directive": {
                    "repair_target": "known_issues",
                    "previous_problem": "known_issues was a string",
                    "required_action": "Return known_issues as a JSON array; use [] when empty.",
                    "must_preserve": ["task objective"],
                    "forbidden": ["changing file_replacements path"],
                    "completion_condition": ["known_issues is an array"],
                }
            }
        },
    }

    prompt = build_worker_prompt(manifest, "INPUT FILES", local_ollama=True)

    assert "THIS IS A REPAIR ATTEMPT" in prompt
    assert "known_issues was a string" in prompt
    assert prompt.index("THIS IS A REPAIR ATTEMPT") < prompt.index("file_replacements is REQUIRED")
