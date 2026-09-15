import pytest

from scripts import devfarm_contracts
from scripts.devfarm import DevFarmError, validate_manifest, validate_patch, validate_result


def test_contract_validation_is_exposed_by_the_standalone_boundary():
    assert validate_manifest is devfarm_contracts.validate_manifest
    assert validate_patch is devfarm_contracts.validate_patch
    assert validate_result is devfarm_contracts.validate_result
    assert devfarm_contracts.DevFarmError is DevFarmError


def test_contract_boundary_rejects_non_mapping_manifest_without_cli_imports():
    with pytest.raises(DevFarmError, match="manifest must be an object"):
        devfarm_contracts.validate_manifest(None)
