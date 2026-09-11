from src.dev_agent.operation import OperationControl
from src.dev_agent.state.control_repository import OperationControl as StateOperationControl


def test_operation_control_is_owned_by_state_control_repository():
    assert OperationControl is StateOperationControl
