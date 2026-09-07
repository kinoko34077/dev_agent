"""Subprocess fixture for real process-death durability testing."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from src.dev_agent.domain.protocol import ModelResponse, Task, ToolCall
from src.dev_agent.providers.base import ModelProvider
from src.dev_agent.runtime import Controller
from src.dev_agent.state import SQLiteStateStore
from src.dev_agent.tools import ToolRegistry, ToolRuntime, ToolSpec


class TwoCallProvider(ModelProvider):
    provider_id = "process-crash"

    def request(self, request):
        return ModelResponse(provider=self.provider_id, model="test", tool_calls=[ToolCall(tool_name="write", arguments={"ordinal": 1}, idempotency_key="proc-1"), ToolCall(tool_name="write", arguments={"ordinal": 2}, idempotency_key="proc-2")], finish_reason="tool_call")


class ExitAfterToolResultStore(SQLiteStateStore):
    def checkpoint(self, **kwargs):
        super().checkpoint(**kwargs)
        if kwargs["phase"] == "after_tool_result":
            os._exit(17)


def main(argv: list[str]) -> int:
    database, marker = map(Path, argv[1:])
    registry = ToolRegistry()

    def write(args):
        marker.write_text(marker.read_text(encoding="utf-8") + str(args["ordinal"]) + "\n" if marker.exists() else str(args["ordinal"]) + "\n", encoding="utf-8")
        return {"ordinal": args["ordinal"]}

    registry.register(ToolSpec(name="write", description="write", side_effect_level="local_write", handler=write))
    with ExitAfterToolResultStore(database) as store:
        Controller(TwoCallProvider(), ToolRuntime(registry), store).run(Task(objective="process crash"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
