from __future__ import annotations

import hashlib
import json

import pytest

from src.dev_agent.compression import (
    CompressionIntegrityError,
    CompressionResult,
    compress_handoff_payload,
    inspect_information_retention,
)
from src.dev_agent.handoff import HandoffDirective, HandoffEnvelope, PayloadMode


class _FakeCompressionService:
    def __init__(self, compressed: str):
        self.compressed = compressed
        self.received: list[tuple[str, str]] = []

    def compress(self, text: str, *, profile: str = "semantic-dense-v1") -> CompressionResult:
        self.received.append((text, profile))
        return CompressionResult(
            compressed_text=self.compressed,
            profile=profile,
            prompt_version=profile,
            model="fake-compressor",
            input_chars=len(text),
            output_chars=len(self.compressed),
            input_sha256=hashlib.sha256(text.encode()).hexdigest(),
            output_sha256=hashlib.sha256(self.compressed.encode()).hexdigest(),
            warnings=(),
        )


def _envelope(payload):
    return HandoffEnvelope(
        kind="analysis_result",
        subject="subject",
        instruction="instruction",
        source_role="planner",
        target_role="reviewer",
        payload=payload,
        original_reference={"artifact_id": "analysis-001"},
    )


def test_compression_only_receives_payload_and_preserves_provenance():
    service = _FakeCompressionService("2026-09-12 commit abcdef1 禁止変更")
    envelope = _envelope("2026-09-12 commit abcdef1 禁止変更 " + ("detail " * 100))

    compressed = compress_handoff_payload(envelope, service, max_uncompressed_chars=20)

    assert service.received[0][0].startswith("2026-09-12")
    assert "instruction" not in service.received[0][0]
    assert compressed.payload_mode == PayloadMode.COMPRESSED.value
    assert compressed.original_reference == {"artifact_id": "analysis-001"}
    assert compressed.original_sha256 == hashlib.sha256(envelope.payload.encode()).hexdigest()
    assert compressed.compression_prompt_version == "semantic-dense-v1"
    assert compressed.compression_model == "fake-compressor"
    assert compressed.payload == "2026-09-12 commit abcdef1 禁止変更"


def test_compression_preserves_directive_but_never_sends_it_to_the_service():
    service = _FakeCompressionService("compressed")
    envelope = HandoffEnvelope(
        kind="analysis_result",
        subject="CONTROL_SUBJECT",
        instruction="CONTROL_INSTRUCTION",
        source_role="planner",
        target_role="reviewer",
        directive=HandoffDirective(
            exclusions=("CONTROL_EXCLUSION",),
            authority_source="current_repository",
            source_requirements=("current_repository",),
            output_contract={"section_detail_policy": {"issues": "detailed"}},
        ),
        payload="payload " * 40,
    )

    compressed = compress_handoff_payload(envelope, service, max_uncompressed_chars=20)

    assert service.received == [(envelope.payload, "semantic-dense-v1")]
    assert compressed.directive == envelope.directive


def test_short_payload_skips_compression():
    service = _FakeCompressionService("should-not-be-called")
    envelope = _envelope("short")

    result = compress_handoff_payload(envelope, service, max_uncompressed_chars=20)

    assert result is envelope
    assert service.received == []


def test_information_retention_reports_missing_numbers_urls_paths_and_negation():
    warnings = inspect_information_retention(
        "2026-09-12 95% https://example.test/a abcdef1234567 src/dev_agent/handoff.py must not change",
        "summary only",
    )

    assert {warning.kind for warning in warnings} >= {
        "date_or_number",
        "url",
        "commit_or_id",
        "path",
        "negation",
    }


def test_strict_integrity_rejects_loss_of_machine_facts():
    service = _FakeCompressionService("summary only")
    envelope = _envelope("2026-09-12 commit abcdef1234567 must not change " + ("detail " * 100))

    with pytest.raises(CompressionIntegrityError, match="information retention"):
        compress_handoff_payload(envelope, service, max_uncompressed_chars=20, strict_integrity=True)


def test_http_response_contract_rejects_digest_mismatch():
    from src.dev_agent.compression.client import CompressionHttpError, parse_compression_response

    value = {
        "compressed_text": "short",
        "profile": "semantic-dense-v1",
        "prompt_version": "semantic-dense-v1",
        "model": "compressor",
        "input_chars": 10,
        "output_chars": 5,
        "input_sha256": "0" * 64,
        "output_sha256": hashlib.sha256(b"short").hexdigest(),
        "warnings": [],
    }
    with pytest.raises(CompressionHttpError, match="input_sha256"):
        parse_compression_response(value, expected_input="different")


def test_http_client_sends_only_fixed_payload_contract():
    from src.dev_agent.compression.client import HttpCompressionService

    original = "2026-09-12 commit abcdef1 must not change"
    compressed = "2026-09-12 abcdef1 禁止変更"
    requests: list[tuple[object, float]] = []

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps(
                {
                    "compressed_text": compressed,
                    "profile": "semantic-dense-v1",
                    "prompt_version": "semantic-dense-v1",
                    "model": "fixed-compressor",
                    "input_chars": len(original),
                    "output_chars": len(compressed),
                    "input_sha256": hashlib.sha256(original.encode()).hexdigest(),
                    "output_sha256": hashlib.sha256(compressed.encode()).hexdigest(),
                    "warnings": [],
                }
            ).encode()

    def _opener(request, *, timeout):
        requests.append((request, timeout))
        return _Response()

    result = HttpCompressionService("https://compress.example/v1/compress", opener=_opener).compress(original)

    request, timeout = requests[0]
    assert timeout == 30.0
    assert json.loads(request.data.decode()) == {"text": original, "profile": "semantic-dense-v1"}
    assert result.compressed_text == compressed
