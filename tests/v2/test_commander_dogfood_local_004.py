from src.dev_agent.operation import OperationConfig


def test_commander_dogfood_public_binding():
    config = OperationConfig()
    assert config.binding_id.startswith(config.provider_id)
