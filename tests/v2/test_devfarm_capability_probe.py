from __future__ import annotations

import json
import subprocess
import sys

import pytest

from scripts.devfarm_capability_probe import ProbeError, probe_specs, run_probe
from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
from src.dev_agent.providers.base import ModelProvider, ProviderError


class _ProbeProvider(ModelProvider):
    provider_id = "probe-provider"
    provider_binding_id = "probe-provider:worker"
    model_id = "probe-model"
    intelligence_tier = "L1"

    def __init__(self, text: str = "A") -> None:
        self.text = text
        self.requests: list[ModelRequest] = []

    def request(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            provider=self.provider_id,
            model=self.model_id,
            text_segments=[self.text],
        )


class _ErrorProvider(_ProbeProvider):
    def request(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        raise ProviderError("quota provider error with secret-looking detail", category="quota")


class _MismatchedProvider(_ProbeProvider):
    def request(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(provider="other-provider", model="other-model", text_segments=["A"])


def test_probe_catalog_is_fixed_and_bounded():
    specs = probe_specs()

    assert [item.level for item in specs] == ["P0", "P1", "P2", "P3", "P4", "P5"]
    assert len(specs) == 6
    with pytest.raises(ProbeError):
        probe_specs(["P0", "P0"])
    with pytest.raises(ProbeError):
        probe_specs(["P9"])


def test_higher_probe_levels_have_concrete_tasks_and_bounded_expected_shapes():
    specs = {item.level: item for item in probe_specs()}

    assert "[[1, 2], [3, 4]]" in specs["P3"].prompt
    assert "[A, B, C]" in specs["P4"].prompt
    assert "budget=" in specs["P5"].prompt and "quality=" in specs["P5"].prompt

    assert run_probe(_ProbeProvider("numpy: [[1, 2], [3, 4]].T -> [[1, 3], [2, 4]]"), specs["P3"])["status"] == "passed"
    assert run_probe(_ProbeProvider("import numpy as np; matrix.T -> [[1 3] [2 4]]"), specs["P3"])["status"] == "passed"
    assert run_probe(_ProbeProvider("C, B, A"), specs["P4"])["status"] == "passed"
    assert run_probe(_ProbeProvider("budget=A; quality=B"), specs["P5"])["status"] == "passed"


def test_probe_exact_response_passes_p0_and_request_is_bounded():
    provider = _ProbeProvider(" A ")

    result = run_probe(provider, probe_specs(["P0"])[0])

    assert result["status"] == "passed"
    assert result["failure_category"] is None
    assert provider.requests[0].max_output_tokens <= 512
    assert provider.requests[0].metadata["probe_level"] == "P0"


def test_probe_output_mismatch_is_separate_from_provider_failure():
    result = run_probe(_ProbeProvider("not A"), probe_specs(["P0"])[0])

    assert result["status"] == "failed"
    assert result["failure_category"] == "model_output_invalid"


def test_probe_provider_error_preserves_category_without_raw_message():
    result = run_probe(_ErrorProvider(), probe_specs(["P0"])[0])

    assert result["status"] == "failed"
    assert result["failure_category"] == "provider_error"
    assert result["provider_error_category"] == "quota"
    assert "secret-looking" not in json.dumps(result, ensure_ascii=False)


def test_probe_identity_mismatch_is_separate_from_output_failure():
    result = run_probe(_MismatchedProvider(), probe_specs(["P0"])[0])

    assert result["status"] == "failed"
    assert result["failure_category"] == "provider_contract_mismatch"


def test_probe_evidence_never_contains_raw_response_text():
    result = run_probe(_ProbeProvider("response contains private raw output"), probe_specs(["P0"])[0])

    assert "private raw output" not in json.dumps(result, ensure_ascii=False)
    assert result["output_chars"] == len("response contains private raw output")


def test_probe_cli_help_is_importable_as_a_script():
    result = subprocess.run(
        [sys.executable, "scripts/devfarm_capability_probe.py", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "fixed bounded DevFarm capability probes" in result.stdout
