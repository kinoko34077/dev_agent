from datetime import datetime, timedelta, timezone
from dev_agent.resources.model_catalog import ModelCatalog


def test_entries_for_binding_deterministic_ordering() -> None:
    now = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    future = now + timedelta(hours=1)

    # Provide entries in deliberately shuffled / non-sorted order
    document = {
        "schema_version": 1,
        "entries": [
            {
                "provider_id": "anthropic",
                "provider_binding_id": "primary",
                "model_id": "claude-3-5-sonnet",
                "source": "test",
                "observed_at": now.isoformat(),
                "expires_at": future.isoformat(),
            },
            {
                "provider_id": "anthropic",
                "provider_binding_id": "primary",
                "model_id": "claude-3-opus",
                "source": "test",
                "observed_at": now.isoformat(),
                "expires_at": future.isoformat(),
            },
            {
                "provider_id": "anthropic",
                "provider_binding_id": "primary",
                "model_id": "claude-3-haiku",
                "source": "test",
                "observed_at": now.isoformat(),
                "expires_at": future.isoformat(),
            },
        ],
    }

    catalog = ModelCatalog.from_document(document)
    entries = catalog.entries_for_binding("anthropic", "primary", now=now)
    model_ids = [entry.model_id for entry in entries]
    assert model_ids == ["claude-3-5-sonnet", "claude-3-haiku", "claude-3-opus"]
