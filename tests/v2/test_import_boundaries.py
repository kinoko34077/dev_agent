import os
from pathlib import Path
import subprocess
import sys


def test_intelligence_leaf_import_does_not_eagerly_load_the_pipeline():
    repo_root = Path(__file__).resolve().parents[2]
    script = (
        "import sys\n"
        "import src.dev_agent.intelligence.routing\n"
        "assert 'src.dev_agent.intelligence.evaluator' not in sys.modules\n"
        "assert 'src.dev_agent.intelligence.execution' not in sys.modules\n"
        "assert 'src.dev_agent.intelligence.workflow' not in sys.modules\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env={**os.environ, "PYTHONPATH": str(repo_root)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_resource_leaf_import_does_not_eagerly_load_budget_and_ledger():
    repo_root = Path(__file__).resolve().parents[2]
    script = (
        "import sys\n"
        "import src.dev_agent.resources.router\n"
        "assert 'src.dev_agent.resources.ledger' not in sys.modules\n"
        "assert 'src.dev_agent.resources.budget' not in sys.modules\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env={**os.environ, "PYTHONPATH": str(repo_root)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_canonical_controller_import_does_not_load_legacy_provider_path():
    repo_root = Path(__file__).resolve().parents[2]
    script = (
        "import sys\n"
        "import src.dev_agent.runtime.controller\n"
        "assert 'src.dev_agent.runtime.legacy_provider' not in sys.modules\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env={**os.environ, "PYTHONPATH": str(repo_root)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_agent_backend_protocol_import_does_not_eagerly_load_dispatcher():
    repo_root = Path(__file__).resolve().parents[2]
    script = (
        "import sys\n"
        "import src.dev_agent.backends.protocol\n"
        "assert 'src.dev_agent.backends.dispatcher' not in sys.modules\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env={**os.environ, "PYTHONPATH": str(repo_root)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_scheduler_queue_import_does_not_eagerly_load_worker_or_quota():
    repo_root = Path(__file__).resolve().parents[2]
    script = (
        "import sys\n"
        "import src.dev_agent.scheduler.queue\n"
        "assert 'src.dev_agent.scheduler.worker' not in sys.modules\n"
        "assert 'src.dev_agent.scheduler.quota' not in sys.modules\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env={**os.environ, "PYTHONPATH": str(repo_root)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_sqlite_state_import_does_not_eagerly_load_json_or_protocol_facades():
    repo_root = Path(__file__).resolve().parents[2]
    script = (
        "import sys\n"
        "import src.dev_agent.state.sqlite_store\n"
        "assert 'src.dev_agent.state.json_store' not in sys.modules\n"
        "assert 'src.dev_agent.state.store' not in sys.modules\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env={**os.environ, "PYTHONPATH": str(repo_root)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
