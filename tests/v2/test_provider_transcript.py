from src.dev_agent.domain.protocol import ModelResponse, Task, TaskStatus, ToolCall
from src.dev_agent.providers.base import ModelProvider
from src.dev_agent.runtime.controller import Controller
from src.dev_agent.state import JsonStateStore
from src.dev_agent.tools import ToolRegistry, ToolRuntime, ToolSpec


class _ToolRoundtripProvider(ModelProvider):
    provider_id = "transcript"

    def __init__(self):
        self.requests = []

    def request(self, request):
        self.requests.append(request)
        if not request.tool_results:
            return ModelResponse(
                provider=self.provider_id,
                model="test",
                tool_calls=[ToolCall(tool_name="echo", arguments={"value": "ok"})],
                finish_reason="tool_calls",
            )
        return ModelResponse(provider=self.provider_id, model="test", text_segments=["complete"])


def test_controller_keeps_assistant_tool_call_before_provider_tool_result(tmp_path):
    provider = _ToolRoundtripProvider()
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="echo",
            description="echo",
            required_arguments=frozenset({"value"}),
            handler=lambda arguments: {"echo": arguments["value"]},
        )
    )
    task = Task(objective="call echo")

    store = JsonStateStore(tmp_path / "state.json")
    result = Controller(provider, ToolRuntime(registry), store).run(task)

    assert result.status == TaskStatus.COMPLETED
    second = provider.requests[1]
    assistant = second.messages[-1]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["function"]["name"] == "echo"
    assert assistant["tool_calls"][0]["function"]["arguments"] == '{"value":"ok"}'
    assert second.tool_results[0].tool_name == "echo"
