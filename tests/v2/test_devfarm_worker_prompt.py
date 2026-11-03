from __future__ import annotations

import json

import pytest

from scripts.devfarm_contracts import WORKER_OUTPUT_MODES
from scripts.devfarm_worker_prompt import (
    _bounded_rework_handoff,
    _compensation_prefix,
    build_worker_prompt,
    repair_system_instruction,
)
from scripts.devfarm_worker_output import minimal_worker_output_schema


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
    assert '"file_replacements":{"<exact path>"' in prompt
    assert "notes is optional" not in prompt
    assert "`notes` as a path" not in prompt
    assert "Do not emit" in prompt
    assert "patch" in prompt
    assert "complete line 1" in prompt
    assert "The host joins" in prompt
    assert "adds one final newline" in prompt
    assert "MUST NOT contain a newline character" in prompt
    assert "the Host derives those facts" in prompt
    assert "Do not emit status, changed_files, tests" in prompt
    assert "The Host will validate the replacement" in prompt
    assert "Preserve every existing public import, class, function, and constant" in prompt
    assert "Do not delete or rename exports" in prompt
    assert "exact allowed output paths" in prompt
    assert "never shorten it to a basename" in prompt
    assert "Escape source quotes and backslashes" in prompt
    assert "no Markdown fence, prose, trailing characters, or second JSON value" in prompt


def test_localized_prompt_explicitly_forbids_trailing_json_or_prose() -> None:
    manifest = {
        "task_id": "task-localized-envelope-1",
        "task_type": "implementation",
        "objective": "make one bounded existing-file change",
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
        localized_proposal=True,
    )

    assert "no Markdown fence, prose, trailing characters, or second JSON value" in prompt


def test_local_ollama_prompt_contains_the_exact_host_owned_output_schema() -> None:
    manifest = {
        "task_id": "task-local-schema-1",
        "task_type": "documentation",
        "objective": "make one bounded documentation change",
        "base_revision": "abc123",
        "allowed_files": ["docs/example.md"],
        "forbidden_files": [".env"],
        "external_provider_allowed": True,
        "approved_provider_ids": ["ollama"],
        "outbound_files": ["docs/example.md"],
        "requirements": ["preserve unrelated content"],
        "acceptance": ["one exact replacement is returned"],
        "test_commands": [],
        "output_contract": {"format": "json"},
    }
    schema = minimal_worker_output_schema(
        allowed_paths=manifest["outbound_files"],
        line_array_only=True,
    )

    prompt = build_worker_prompt(
        manifest,
        "--- BEGIN FILE docs/example.md ---\nold\n--- END FILE ---",
        local_ollama=True,
    )

    assert "HOST-OWNED RESPONSE JSON SCHEMA" in prompt
    assert json.dumps(schema, ensure_ascii=False, sort_keys=True) in prompt


def test_local_ollama_prompt_uses_compact_manifest_projection() -> None:
    manifest = {
        "task_id": "task-local-compact-1",
        "task_type": "test",
        "objective": "add one tiny regression test",
        "base_revision": "abc123",
        "allowed_files": ["tests/v2/test_target.py"],
        "forbidden_files": [".env", "spec/v2/GATE_STATUS.json", "scripts/devfarm_worker.py"],
        "external_provider_allowed": True,
        "approved_provider_ids": ["ollama"],
        "outbound_files": [],
        "new_files": ["tests/v2/test_target.py"],
        "requirements": ["keep the file tiny"],
        "acceptance": ["the focused test passes"],
        "test_commands": ["python -m pytest tests/v2/test_target.py -q"],
        "output_contract": {"mode": "minimal_file_replacement"},
    }

    prompt = build_worker_prompt(manifest, "INPUT FILES", local_ollama=True)

    assert '"task_id": "task-local-compact-1"' in prompt
    assert '"forbidden_files"' not in prompt
    assert '"test_commands"' not in prompt


def test_local_ollama_prompt_treats_requirements_as_literal_contract() -> None:
    manifest = {
        "task_id": "task-local-literal-1",
        "task_type": "test",
        "objective": "add one tiny regression test",
        "base_revision": "abc123",
        "allowed_files": ["tests/v2/test_target.py"],
        "forbidden_files": [".env"],
        "external_provider_allowed": True,
        "approved_provider_ids": ["ollama"],
        "outbound_files": [],
        "new_files": ["tests/v2/test_target.py"],
        "requirements": [
            'Call build_worker_provider("gemini", "gemini-3.6-flash", 30.0, "gemini:worker:free-3") exactly.',
        ],
        "acceptance": ["the focused test passes"],
        "test_commands": [],
        "output_contract": {"mode": "minimal_file_replacement"},
    }

    prompt = build_worker_prompt(manifest, "INPUT FILES", local_ollama=True)

    assert "REQUIREMENTS ARE EXECUTABLE CONSTRAINTS" in prompt
    assert "Do not invent or simplify callable signatures" in prompt
    assert "Do not emit trailing whitespace" in prompt


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
                },
                "repair_context": {
                    "historical_constraints": {
                        "resolved_must_not_regress": ["embedded newline remains forbidden"]
                    }
                },
            }
        },
    }

    prompt = build_worker_prompt(manifest, "INPUT FILES", local_ollama=True)

    assert "THIS IS A REPAIR ATTEMPT" in prompt
    assert "known_issues was a string" in prompt
    assert "CURRENT REQUIRED CORRECTION" in prompt
    assert "DO NOT REGRESS" in prompt
    assert "embedded newline remains forbidden" in prompt
    assert prompt.index("THIS IS A REPAIR ATTEMPT") < prompt.index("file_replacements is REQUIRED")


def test_rework_system_instruction_repeats_only_host_owned_correction() -> None:
    manifest = {
        "rework_handoff": {
            "repair_directive": {
                "repair_target": "known_issues",
                "required_action": "Return known_issues as a JSON array; use [] when empty.",
            },
            "repair_context": {
                "raw_traceback": "must not cross the boundary",
            },
        }
    }

    instruction = repair_system_instruction(manifest)

    assert instruction is not None
    assert "Host-directed repair" in instruction
    assert "known_issues" in instruction
    assert "use [] when empty" in instruction
    assert "must not cross the boundary" not in instruction


def test_rework_prompt_includes_only_host_selected_compensation_contract() -> None:
    manifest = {
        "task_id": "task-compensation-prompt-1",
        "task_type": "implementation",
        "objective": "make one bounded existing-file change",
        "base_revision": "abc123",
        "allowed_files": ["src/example.py"],
        "forbidden_files": [".env"],
        "external_provider_allowed": True,
        "approved_provider_ids": ["ollama"],
        "outbound_files": ["src/example.py"],
        "requirements": ["preserve unrelated behavior"],
        "acceptance": ["focused test passes"],
        "test_commands": ["python -m compileall -q src/example.py"],
        "output_contract": {"format": "json"},
        "rework_handoff": {
            "repair_context": {
                "compensation": {
                    "authority": "host_policy",
                    "action": "switch_output_mode",
                    "prompt_contract": "host-localized-edit",
                    "output_mode": "localized_file_edit",
                    "expected_output_chars": 4096,
                    "context_hints": ["exact_line_anchor", "expected_lines"],
                    "raw_traceback": "must never be copied",
                },
                "historical_constraints": {},
            },
            "repair_directive": {
                "repair_target": "file_edits",
                "previous_problem": "the previous response used the wrong shape",
                "required_action": "return one anchored edit",
                "must_preserve": ["public exports"],
                "forbidden": ["new files"],
                "completion_condition": ["expected_lines match the base"],
            },
        },
    }

    prompt = build_worker_prompt(manifest, "INPUT FILES", local_ollama=True, localized_proposal=True)

    assert "HOST-SELECTED CORRECTION ROUTE" in prompt
    assert "RESPONSIBILITY ACTION: switch_output_mode" in prompt
    assert "PROMPT CONTRACT: host-localized-edit" in prompt
    assert "OUTPUT MODE: localized_file_edit" in prompt
    assert "MAX CHANGE BUDGET (characters): 4096" in prompt
    assert "BOUNDED CONTEXT HINTS: exact_line_anchor, expected_lines" in prompt
    assert "must never be copied" not in prompt


def test_host_compensation_output_modes_follow_manifest_contract() -> None:
    for mode in sorted(WORKER_OUTPUT_MODES):
        context = {
            "compensation": {
                "authority": "host_policy",
                "action": "switch_output_mode",
                "prompt_contract": "host-localized-edit",
                "output_mode": mode,
                "context_hints": [],
            }
        }
        assert f"OUTPUT MODE: {mode}" in _compensation_prefix(context)
        bounded = _bounded_rework_handoff({"rework_handoff": {"repair_context": context}})
        assert bounded is not None
        assert bounded["repair_context"]["compensation"]["output_mode"] == mode

    for unsupported in ("unknown_mode", 123):
        context = {
            "compensation": {
                "authority": "host_policy",
                "action": "switch_output_mode",
                "prompt_contract": "host-localized-edit",
                "output_mode": unsupported,
                "context_hints": [],
            }
        }
        assert _compensation_prefix(context) == ""
        assert _bounded_rework_handoff({"rework_handoff": {"repair_context": context}}) is None

@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("action", []),
        ("action", {"unexpected": "list"}),
        ("prompt_contract", ["host-localized-edit"]),
        ("output_mode", ["localized_file_edit"]),
        ("output_mode", {"mode": "localized_file_edit"}),
        ("output_mode", 123),
    ],
)
def test_untrusted_host_compensation_fields_fail_closed(field: str, invalid: object) -> None:
    compensation: dict[str, object] = {
        "authority": "host_policy",
        "action": "switch_output_mode",
        "prompt_contract": "host-localized-edit",
        "output_mode": "localized_file_edit",
        "context_hints": [],
    }
    compensation[field] = invalid
    context = {"compensation": compensation}

    assert _compensation_prefix(context) == ""
    assert _bounded_rework_handoff({"rework_handoff": {"repair_context": context}}) is None
