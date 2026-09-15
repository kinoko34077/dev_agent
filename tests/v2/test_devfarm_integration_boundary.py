from __future__ import annotations


def test_host_integration_service_exposes_read_and_write_boundaries():
    from scripts.devfarm_integration import integrate_worker, verified_worker_patch

    assert callable(integrate_worker)
    assert callable(verified_worker_patch)
