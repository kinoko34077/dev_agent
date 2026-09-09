import pytest

from scripts.devfarm import DevFarmError, validate_manifest, validate_result


def _manifest():
    return {
        "task_id": "groq-quota-001",
        "objective": "add a Groq quota header parser",
        "base_revision": "e410a584e94e546b52b86536ae1f7267fe6fda3f",
        "allowed_files": ["src/dev_agent/providers/groq/provider.py", "tests/v2/test_groq_provider.py"],
        "read_files": ["src/dev_agent/providers/base.py"],
        "forbidden_files": ["spec/v2/GATE_STATUS.json", "src/dev_agent/resources/budget.py"],
        "external_provider_allowed": True,
        "approved_provider_ids": ["cloudflare"],
        "outbound_files": ["src/dev_agent/providers/groq/provider.py"],
        "requirements": ["normalize remaining/reset"],
        "acceptance": ["missing values remain unknown"],
        "test_commands": ["python -m pytest -q tests/v2/test_groq_provider.py"],
        "max_attempts": 2,
        "output_contract": {"files": ["result.json", "patch.diff", "tests.json", "notes.md"]},
    }


def test_devfarm_manifest_has_narrow_file_ownership_and_normalized_paths():
    manifest = validate_manifest(_manifest())

    assert manifest["task_id"] == "groq-quota-001"
    assert manifest["allowed_files"] == ["src/dev_agent/providers/groq/provider.py", "tests/v2/test_groq_provider.py"]
    assert set(manifest["allowed_files"]).isdisjoint(manifest["forbidden_files"])


def test_devfarm_manifest_rejects_path_escape_and_ownership_overlap():
    escaping = _manifest()
    escaping["allowed_files"] = ["../README.md"]
    with pytest.raises(DevFarmError, match="relative"):
        validate_manifest(escaping)

    overlap = _manifest()
    overlap["forbidden_files"] = [overlap["allowed_files"][0]]
    with pytest.raises(DevFarmError, match="both allowed and forbidden"):
        validate_manifest(overlap)


def test_devfarm_manifest_rejects_protected_runtime_ownership_even_when_omitted_from_forbidden():
    protected = _manifest()
    protected["allowed_files"] = ["spec/v2/GATE_STATUS.json"]
    protected["outbound_files"] = ["spec/v2/GATE_STATUS.json"]
    with pytest.raises(DevFarmError, match="protected"):
        validate_manifest(protected)

    recovery = _manifest()
    recovery["allowed_files"] = ["recovery/git_recovery.py"]
    recovery["outbound_files"] = ["recovery/git_recovery.py"]
    with pytest.raises(DevFarmError, match="protected"):
        validate_manifest(recovery)


def test_devfarm_result_must_match_manifest_base_and_allowed_files():
    manifest = validate_manifest(_manifest())
    result = {
        "status": "completed",
        "base_revision": manifest["base_revision"],
        "changed_files": ["src/dev_agent/providers/groq/provider.py"],
        "tests_run": ["python -m pytest -q tests/v2/test_groq_provider.py"],
        "tests_passed": True,
        "known_issues": [],
        "assumptions": [],
    }

    assert validate_result(result, manifest=manifest)["tests_passed"] is True

    result["changed_files"] = ["src/dev_agent/resources/budget.py"]
    with pytest.raises(DevFarmError, match="allowed_files"):
        validate_result(result, manifest=manifest)

    result["changed_files"] = []
    result["base_revision"] = "0" * 40
    with pytest.raises(DevFarmError, match="base_revision"):
        validate_result(result, manifest=manifest)


def test_devfarm_result_revalidates_manifest_before_using_task_path():
    manifest = _manifest()
    manifest["task_id"] = "../escape"
    result = {
        "status": "completed",
        "base_revision": manifest["base_revision"],
        "changed_files": [],
        "tests_run": [],
        "tests_passed": True,
        "known_issues": [],
        "assumptions": [],
    }
    with pytest.raises(DevFarmError, match="unsafe"):
        validate_result(result, manifest=manifest)


def test_devfarm_manifest_requires_explicit_external_send_boundary():
    manifest = _manifest()
    manifest.pop("external_provider_allowed")
    with pytest.raises(DevFarmError, match="external_provider_allowed"):
        validate_manifest(manifest)

    manifest = _manifest()
    manifest["outbound_files"] = ["README.md"]
    with pytest.raises(DevFarmError, match="outbound_files"):
        validate_manifest(manifest)


def test_devfarm_manifest_rejects_protected_outbound_source():
    manifest = _manifest()
    manifest["read_files"] = [".env.local"]
    manifest["outbound_files"] = [".env.local"]
    with pytest.raises(DevFarmError, match="protected"):
        validate_manifest(manifest)
