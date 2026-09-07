import importlib
import sys


def test_v2_bootstrap_environment():
    """Phase 0 smoke test: the alpha0 dependency boundary is available."""
    assert sys.version_info >= (3, 10)
    for module_name in (
        "dataclasses",
        "datetime",
        "json",
        "pathlib",
        "sqlite3",
        "typing",
        "uuid",
    ):
        assert importlib.util.find_spec(module_name) is not None
