import inspect

from scripts.devfarm_supervisor import main as compatibility_main
from scripts.devfarm_supervisor_cli import main as cli_main


def test_supervisor_cli_entrypoint_is_owned_by_dedicated_cli_module():
    assert cli_main.__module__ == "scripts.devfarm_supervisor_cli"
    assert "devfarm_supervisor_cli" in inspect.getsource(compatibility_main)
