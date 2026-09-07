import json
from pathlib import Path

import pytest

from src.dev_agent.domain.protocol import ModelRequest, ToolResult
from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.gemini.decoder import decode_generate_content
from src.dev_agent.providers.ollama.provider import OllamaProvider


def test_gemini_rest_function_call_fixture_decodes_to_normalized_tool_call():
    raw = json.loads((Path(__file__).parent / "fixtures" / "gemini" / "function_call_response.json").read_text(encoding="utf-8"))
    response = decode_generate_content(raw, model="gemini-test", request_id="b9913466-2d8b-4da3-a665-47fd8ddf5819")
    assert response.tool_calls[0].tool_name == "echo"
    assert response.tool_calls[0].arguments == {"value": "fixture"}


def test_gemini_rest_malformed_response_is_classified():
    with pytest.raises(ProviderError, match="missing candidate content"):
        decode_generate_content({"candidates": []}, model="gemini-test")


def test_ollama_payload_preserves_normalized_tool_result_identity():
    provider = OllamaProvider(model="local-test")
    request = ModelRequest(task_id=_id(), messages=[{"role": "user", "content": "x"}], tool_results=[ToolResult(call_id=_id(), tool_name="echo", structured_result={"value": "ok"})])
    payload = provider._payload(request)
    assert payload["options"]["num_predict"] == request.max_output_tokens
    tool_message = payload["messages"][-1]
    assert tool_message["tool_name"] == "echo"
    assert json.loads(tool_message["content"])["call_id"] == request.tool_results[0].call_id


def _id():
    from uuid import uuid4

    return str(uuid4())
