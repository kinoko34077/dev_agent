import asyncio

from src.dev_agent.discord.delivery import DiscordHumanFacingSender
from src.dev_agent.discord.renderer import (
    render_echo,
    render_human_request,
    render_read_projection,
)
from src.dev_agent.human import HumanRequest


class _Typing:
    def __init__(self, events):
        self.events = events

    async def __aenter__(self):
        self.events.append("typing_enter")

    async def __aexit__(self, *_args):
        self.events.append("typing_exit")


class _Channel:
    id = 20

    def __init__(self, events):
        self.events = events

    def typing(self):
        return _Typing(self.events)

    async def send(self, content, **kwargs):
        self.events.append(("send", content, kwargs))
        await asyncio.sleep(0)
        return "900"


def test_human_sender_types_then_sends_after_injected_delay():
    events = []
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    sender = DiscordHumanFacingSender(
        typing_delay_seconds=2.0,
        sleep=fake_sleep,
    )

    asyncio.run(sender.send(_Channel(events), "hello"))

    assert events == [
        "typing_enter",
        ("send", "hello", {}),
        "typing_exit",
    ]
    assert sleeps == [2.0]


def test_human_sender_serializes_same_channel():
    events = []
    sender = DiscordHumanFacingSender(typing_delay_seconds=0.0)
    channel = _Channel(events)

    async def run():
        await asyncio.gather(sender.send(channel, "first"), sender.send(channel, "second"))

    asyncio.run(run())

    assert [item[1] for item in events if isinstance(item, tuple) and item[0] == "send"] == [
        "first",
        "second",
    ]


def test_renderers_use_short_sections_and_do_not_echo_human_text():
    assert render_echo("a very long human message") == "受信しました。\n作業を開始します。"
    status = render_read_projection({"current_task": "README", "current_action": "検証"})
    assert status.startswith("現在の状態")
    assert "- Task: README" in status
    assert "- 処理: 検証" in status

    request = HumanRequest(
        request_id="request-1",
        root_id="root-1",
        task_id="task-1",
        attempt_id="attempt-1",
        reason="仕様判断",
        question="AとBのどちらですか？",
        allowed_answers=("A", "B"),
    )
    rendered = render_human_request(request)
    assert "Request:" not in rendered
    assert "- 理由: 仕様判断" in rendered
    assert "- 質問: AとBのどちらですか？" in rendered
