import inspect

from src.dev_agent.runtime.controller import Controller


def test_controller_run_delegates_model_turn_preparation_to_phase_helpers():
    source = inspect.getsource(Controller._run)

    assert "self._prepare_model_step(" in source
    assert "self._build_model_request(" in source
    assert "self._record_model_request(" in source
