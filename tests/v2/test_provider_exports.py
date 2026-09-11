import os
from pathlib import Path
import subprocess
import sys

from src.dev_agent import providers


def test_provider_package_exports_all_provider_and_dispatch_symbols():
    exported = set(providers.__all__)

    assert {"ModelProvider", "GroqHttpProvider", "MistralHttpProvider", "OpenRouterHttpProvider", "ProviderDispatchJournal", "SambaNovaHttpProvider", "ProviderDispatcher", "ProviderRegistry"} <= exported
    assert len(providers.__all__) == len(set(providers.__all__))


def test_factory_import_does_not_eagerly_import_all_provider_adapters():
    repo_root = Path(__file__).resolve().parents[2]
    script = (
        "import sys\n"
        "import src.dev_agent.providers.factory\n"
        "assert 'src.dev_agent.providers.cloudflare' not in sys.modules\n"
        "assert 'src.dev_agent.providers.gemini' not in sys.modules\n"
        "assert 'src.dev_agent.providers.openrouter' not in sys.modules\n"
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
