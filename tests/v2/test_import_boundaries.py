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
