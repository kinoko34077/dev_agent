import pytest
from threading import Event as ThreadEvent, Thread
from time import sleep, time
import os
import subprocess
import sys

from src.dev_agent.domain.protocol import Event, ExecutionLimits, ModelResponse, Step, StepStatus, Task, TaskStatus, ToolCall, ToolResult, ToolResultStatus
from src.dev_agent.policy import PathPolicy
from src.dev_agent.policy.approvals import canonical_arguments_hash
from src.dev_agent.providers.base import ModelProvider
from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.fake.provider import FakeProvider
from src.dev_agent.runtime import Controller, RuntimeFailure
from src.dev_agent.state import JsonStateStore, SQLiteStateStore
from src.dev_agent.tools import ToolRegistry, ToolRuntime, ToolSpec



from .integration_hardening_support import *

def test_event_artifact_store_enforces_expiry_and_content_integrity(tmp_path, monkeypatch):
    import src.dev_agent.security.event_artifacts as artifact_module

    store = artifact_module.EventArtifactStore(tmp_path / "artifacts")
    reference = store.put(b'{"message":"safe"}', content_type="application/json", retention_seconds=60)
    monkeypatch.setattr(artifact_module.time, "time", lambda: reference["expires_at"] + 1)
    with pytest.raises(FileNotFoundError):
        store.read(reference["uri"])
    monkeypatch.undo()

    payload_path, _ = store._paths(reference["uri"])
    payload_path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        store.read(reference["uri"])

def test_event_artifact_store_keeps_sanitized_oversized_payloads_bound_and_expirable(tmp_path):
    from src.dev_agent.runtime import controller as controller_module

    artifact_store_type = getattr(controller_module, "EventArtifactStore", None)
    assert artifact_store_type is not None
    store = artifact_store_type(tmp_path / "artifacts")
    payload = b'{"message":"safe"}'
    reference = store.put(payload, content_type="application/json", retention_seconds=60)
    assert reference["uri"].startswith("event-artifact-sha256:")
    assert store.read(reference["uri"]) == payload
    assert store.purge(now=reference["expires_at"] + 1) == 1
    with pytest.raises(FileNotFoundError):
        store.read(reference["uri"])

def test_event_payload_redacts_secrets_and_caps_large_strings():
    safe = Controller._safe_event_payload({"api_key": "fake-secret", "nested": {"password": "pw"}, "blob": "x" * 5000})
    assert safe["api_key"] == "[REDACTED]"
    assert safe["nested"]["password"] == "[REDACTED]"
    assert safe["blob"].endswith("...[TRUNCATED]")
    assert len(safe["blob"]) == 4096 + len("...[TRUNCATED]")

def test_controller_writes_sanitized_oversized_event_to_artifact_store(tmp_path):
    from src.dev_agent.providers.fake import FakeProvider
    from src.dev_agent.security.event_artifacts import EventArtifactStore

    store = JsonStateStore(tmp_path / "event-state.json")
    controller = Controller(FakeProvider(), ToolRuntime(ToolRegistry()), store, event_artifacts=EventArtifactStore(tmp_path / "event-artifacts"))
    task = Task(objective="artifact event")
    event = controller._event_record(task, "large.event", {"content": ["safe-" + ("x" * 4_096)] * 20})
    assert event.payload["payload_ref"].startswith("event-artifact-sha256:")
    artifact_root = tmp_path / "event-artifacts"
    assert list(artifact_root.glob("*.bin"))

def test_event_payload_detects_secret_values_and_total_byte_cap():
    safe = Controller._safe_event_payload({"content": "bearer abcdefghijklmnop", "large": ["x" * 4096] * 20})
    assert safe["_truncated"] is True
    assert safe["payload_ref"].startswith("event-sha256:")
    assert "abcdefghijklmnop" not in str(safe)

def test_event_artifact_store_rejects_path_traversal_and_secret_payloads(tmp_path):
    from src.dev_agent.runtime import controller as controller_module

    store = controller_module.EventArtifactStore(tmp_path / "artifacts")
    with pytest.raises(ValueError, match="artifact reference"):
        store.read("event-artifact-sha256:../escape")
    with pytest.raises(ValueError, match="sanitized"):
        store.put(b"api_key=raw-secret", content_type="text/plain", retention_seconds=60)
