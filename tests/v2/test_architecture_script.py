from pathlib import Path
import subprocess
import sys
import ast

from scripts.check_architecture import _devfarm_contract_imports, _private_devfarm_imports


def test_architecture_script_passes_current_dependency_boundaries():
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(repo_root / "scripts" / "check_architecture.py")],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_architecture_rejects_private_cross_devfarm_imports():
    tree = ast.parse("from scripts.devfarm_worker import _provider, build_worker_provider")

    assert _private_devfarm_imports("scripts.devfarm_supervisor", tree) == (
        ("scripts.devfarm_worker", "_provider"),
    )


def test_architecture_rejects_contract_imports_from_cli_barrel():
    tree = ast.parse("from scripts.devfarm import validate_manifest, write_result")

    assert _devfarm_contract_imports("scripts.devfarm_worker", tree) == ("validate_manifest",)
