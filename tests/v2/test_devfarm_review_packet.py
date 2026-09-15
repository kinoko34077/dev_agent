from __future__ import annotations


def test_review_packet_builder_is_a_public_read_only_boundary():
    from scripts.devfarm_review_packet import build_review_packet

    assert callable(build_review_packet)


def test_review_packet_and_worker_use_shared_repository_json_reader():
    from scripts.devfarm_repository import read_json
    from scripts.devfarm_review_packet import read_json as packet_read_json
    from scripts.devfarm_worker import read_json as worker_read_json

    assert packet_read_json is read_json
    assert worker_read_json is read_json
