from __future__ import annotations


def test_review_packet_builder_is_a_public_read_only_boundary():
    from scripts.devfarm_review_packet import build_review_packet

    assert callable(build_review_packet)
