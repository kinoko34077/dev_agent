from src.dev_agent.resources import schema
from src.dev_agent.resources.ledger import ResourceLedger


def test_resource_ledger_schema_is_owned_by_schema_module():
    assert ResourceLedger.SCHEMA_VERSION == schema.SCHEMA_VERSION
    assert ResourceLedger._SCHEMA is schema.SCHEMA
