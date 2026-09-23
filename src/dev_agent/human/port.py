"""Transport-neutral Human interaction port."""

from __future__ import annotations

from typing import Protocol

from .contracts import HumanRequest, HumanResponse


class HumanInteractionPort(Protocol):
    """Durable Human request boundary; transports implement this protocol."""

    def request_human(self, request: HumanRequest) -> None:
        ...

    def poll_response(self, request_id: str) -> HumanResponse | None:
        ...

    def consume_response(self, request_id: str) -> HumanResponse:
        ...


class SQLiteHumanInteractionPort:
    """Default durable port backed by the existing SQLite StateStore."""

    def __init__(self, store) -> None:
        required = ("save_human_request", "get_human_response", "consume_human_response")
        if any(not callable(getattr(store, name, None)) for name in required):
            raise TypeError("store does not implement the Human interaction persistence contract")
        self._store = store

    def request_human(self, request: HumanRequest) -> None:
        self._store.save_human_request(request)

    def poll_response(self, request_id: str) -> HumanResponse | None:
        return self._store.get_human_response(request_id)

    def consume_response(self, request_id: str) -> HumanResponse:
        return self._store.consume_human_response(request_id)


__all__ = ["HumanInteractionPort", "SQLiteHumanInteractionPort"]
