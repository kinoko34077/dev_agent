from scripts.devfarm_supervisor_metadata import (
    advance_heartbeat,
    normalize_supervisor_metadata,
    select_heartbeat_cadence,
)
from scripts.devfarm_supervisor_protocol import (
    advance_heartbeat as compatibility_advance_heartbeat,
    normalize_supervisor_metadata as compatibility_normalize_metadata,
    select_heartbeat_cadence as compatibility_select_cadence,
)


def test_supervisor_metadata_has_a_neutral_public_boundary() -> None:
    assert compatibility_advance_heartbeat is advance_heartbeat
    assert compatibility_normalize_metadata is normalize_supervisor_metadata
    assert compatibility_select_cadence is select_heartbeat_cadence

    metadata = normalize_supervisor_metadata({"status": "WAITING_FOR_WORKER"})
    assert metadata["status"] == "WAITING_FOR_WORKER"
    assert select_heartbeat_cadence(301) == 5
    assert advance_heartbeat(metadata, unchanged=False)["unchanged_check_count"] == 0
