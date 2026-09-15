from __future__ import annotations

from scripts.devfarm_worker_input import build_worker_input_context


def test_worker_input_composition_uses_host_reader_and_returns_egress_manifest() -> None:
    manifest = {
        "task_id": "task-input",
        "base_revision": "revision-1",
        "outbound_files": ["src/example.py"],
        "approved_provider_ids": ["gemini"],
    }
    calls: list[tuple[str, str]] = []

    def reader(revision: str, relative: str) -> bytes:
        calls.append((revision, relative))
        return b"print('bounded')\n"

    context, egress = build_worker_input_context(manifest, read_file_at_revision=reader)

    assert calls == [("revision-1", "src/example.py")]
    assert "BEGIN FILE src/example.py" in context
    assert egress.decision.value == "ALLOW"
    assert egress.destination == "gemini"
    assert egress.files[0].path == "src/example.py"
