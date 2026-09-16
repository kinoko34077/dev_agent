import pytest

from scripts import devfarm_contracts
from scripts.devfarm import DevFarmError, validate_manifest, validate_patch, validate_result
from scripts.devfarm_contracts import parse_host_test_command


def test_contract_validation_is_exposed_by_the_standalone_boundary():
    assert validate_manifest is devfarm_contracts.validate_manifest
    assert validate_patch is devfarm_contracts.validate_patch
    assert validate_result is devfarm_contracts.validate_result
    assert devfarm_contracts.DevFarmError is DevFarmError


def test_contract_boundary_rejects_non_mapping_manifest_without_cli_imports():
    with pytest.raises(DevFarmError, match="manifest must be an object"):
        devfarm_contracts.validate_manifest(None)


@pytest.mark.parametrize(
    "command",
    [
        "python -m pytest C:/outside/test_target.py -q",
        r"python -m pytest C:\outside\test_target.py -q",
        "python -m pytest C:outside/test_target.py -q",
    ],
)
def test_host_test_command_rejects_windows_drive_paths(command: str) -> None:
    with pytest.raises(DevFarmError, match="paths must stay relative"):
        parse_host_test_command(command)
