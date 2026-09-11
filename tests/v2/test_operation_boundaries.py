from src.dev_agent.operation import OperationControl
from src.dev_agent.state.control_repository import OperationControl as StateOperationControl


def test_operation_control_is_owned_by_state_control_repository():
    assert OperationControl is StateOperationControl


def test_cli_parser_is_available_from_the_dedicated_cli_boundary():
    from src.dev_agent.cli import build_parser

    arguments = build_parser().parse_args(["status", "task-1"])

    assert arguments.command == "status"
    assert arguments.task_id == "task-1"
