"""Bounded, secret-free structural diagnostics for provider decode failures."""

import json
from uuid import uuid4

import pytest

from src.dev_agent.domain.protocol import ModelRequest
from src.dev_agent.providers.base import ProviderError, project_decode_structure
from src.dev_agent.providers.cloudflare.provider import CloudflareWorkersAIHttpProvider
from src.dev_agent.providers.gemini.decoder import decode_generate_content


def test_decode_structure_projects_shape_without_raw_values():
    secret = "api-key-should-never-cross-the-diagnostic-boundary"
    diagnostic = project_decode_structure(
        {
            "candidates": [],
            "apiKey": secret,
            "nested": {"parts": [{"text": secret}]},
        },
        decoder_branch="gemini.generate_content",
        http_status=503,
        content_type="application/json; charset=utf-8",
    )

    encoded = json.dumps(diagnostic, sort_keys=True)
    assert diagnostic["decoder_branch"] == "gemini.generate_content"
    assert diagnostic["http_status"] == 503
    assert diagnostic["content_type"] == "application/json"
    assert "candidates" in diagnostic["top_level_keys"]
    assert diagnostic["size_bucket"] in {"empty", "tiny", "small", "medium", "large", "oversized"}
    assert len(diagnostic["schema_fingerprint"]) == 16
    assert secret not in encoded
    assert "nested.parts" in encoded


def test_gemini_decode_failure_carries_bounded_structure_only():
    secret = "gemini-secret-value"
    with pytest.raises(ProviderError) as caught:
        decode_generate_content(
            {"candidates": [], "apiKey": secret, "private": {"value": secret}},
            model="gemini-test",
        )

    error = caught.value
    assert error.category == "provider_decode"
    assert error.decode_diagnostics["decoder_branch"] == "gemini.generate_content"
    assert "candidates" in error.decode_diagnostics["top_level_keys"]
    assert secret not in json.dumps(error.decode_diagnostics, sort_keys=True)
    assert secret not in str(error)


def test_cloudflare_decode_failure_carries_bounded_structure_only():
    secret = "cloudflare-token-value"
    request = ModelRequest(task_id=str(uuid4()), messages=[{"role": "user", "content": "hello"}])
    with pytest.raises(ProviderError) as caught:
        CloudflareWorkersAIHttpProvider._decode(
            {"success": True, "result": None, "apiToken": secret, "private": {"value": secret}},
            request,
            model="cloudflare-test",
        )

    error = caught.value
    assert error.category == "provider_decode"
    assert error.decode_diagnostics["decoder_branch"] == "cloudflare.chat_completion"
    assert "result" in error.decode_diagnostics["top_level_keys"]
    assert secret not in json.dumps(error.decode_diagnostics, sort_keys=True)
    assert secret not in str(error)
