"""Bounded, redacted Discord-facing projections."""

from __future__ import annotations

from collections.abc import Mapping

from ..human import HumanRequest
from ..security.audit import AuditRecorder


_MAX_DISCORD_TEXT = 2_000
_MAX_DETAIL = 800

_STAGE_LABELS = {
    "accepted": "依頼を受理しました",
    "planning": "計画を作成しています",
    "host_verification": "Host Verification中",
    "review": "Reviewer確認中",
    "integration": "統合中",
    "continuation": "後続Taskを解放しました",
    "waiting_human": "Humanの判断を待っています",
    "reconciliation": "外部結果の照合を待っています",
    "completed": "作業が完了しました",
}


def _safe_text(value: object, *, maximum: int = _MAX_DETAIL) -> str:
    raw = value if isinstance(value, str) else str(value)
    safe = AuditRecorder.sanitize_payload({"value": raw})["value"]
    return str(safe).strip()[:maximum]


def _bounded(value: str) -> str:
    return value[:_MAX_DISCORD_TEXT]


def render_echo(content: str) -> str:
    # The Human already sees their own message in Discord; do not duplicate
    # untrusted input in the Bot response.
    _ = content
    return "受信しました。\n作業を開始します。"


def render_ingress_ack(kind: str, *, result: object | None = None) -> str:
    """Render a short acknowledgement for an accepted ingress event."""

    labels = {
        "CHAT": "了解しました。",
        "NEW_REQUEST": "受信しました。\n作業を開始します。",
        "NOTE": "追加指示を受け付けました。\n次の安全な区切りから反映します。",
        "PARALLEL": "並行作業を受け付けました。\n既存の作業とは分けて進めます。",
        "INTERRUPT": "割り込みを受け付けました。\n安全な区切りで切り替えます。",
        "CANCEL": "停止依頼を受け付けました。\n対象Taskだけを停止します。",
        "WAIT": "待機を受け付けました。\n指定時刻以降に作業を再開します。",
    }
    if str(kind).upper() == "WAIT" and isinstance(result, Mapping):
        state = result.get("state")
        seconds = result.get("delay_seconds")
        if state == "WAIT_ACCEPTED":
            return f"{_safe_text(seconds, maximum=16)}秒待機します。\n指定時刻以降に作業を再開します。"
        if state == "WAIT_DEFERRED":
            return f"現在の処理が安全な区切りに到達したら{_safe_text(seconds, maximum=16)}秒待機します。\nまだ待機開始前です。"
        if state == "WAIT_FAILED":
            return "待機を設定できませんでした。\n現在の処理は継続しています。"
    if str(kind).upper() == "WAIT" and result is not None:
        metadata = getattr(result, "metadata", None)
        if isinstance(metadata, Mapping) and metadata.get("wait_reason") == "user_delay":
            seconds = metadata.get("requested_duration_seconds")
            if isinstance(seconds, int) and not isinstance(seconds, bool):
                return f"{_safe_text(seconds, maximum=16)}秒待機します。\n指定時刻以降に作業を再開します。"
    return labels.get(str(kind).upper(), render_echo(""))


def render_progress(stage: str, *, detail: str | None = None) -> str:
    label = _STAGE_LABELS.get(stage.casefold(), "作業状態が更新されました")
    lines = ["状態が更新されました", "", f"- 状態: {label}"]
    if detail:
        lines.append(f"- 詳細: {_safe_text(detail)}")
    return _bounded("\n".join(lines))


def render_human_request(request: HumanRequest) -> str:
    if not isinstance(request, HumanRequest):
        raise TypeError("request must be HumanRequest")
    answers = " / ".join(_safe_text(answer, maximum=120) for answer in request.allowed_answers)
    suffix = f"\n選択肢: {answers}" if answers else ""
    rendered = (
        "判断が必要です\n"
        f"\n- 理由: {_safe_text(request.reason)}\n"
        f"- 質問: {_safe_text(request.question)}"
        f"{suffix}"
    )
    return _bounded(rendered)


def render_read_projection(projection: Mapping[str, object]) -> str:
    if not isinstance(projection, Mapping):
        raise TypeError("projection must be a mapping")
    labels = (
        ("root", "root"),
        ("work_address", "位置"),
        ("current_task", "Task"),
        ("current_action", "処理"),
        ("verification", "検証"),
        ("blocked_reason", "停止理由"),
        ("next_action", "次の処理"),
    )
    lines = []
    for key, label in labels:
        if key in projection and projection[key] is not None:
            lines.append(f"{label}: {_safe_text(projection[key], maximum=300)}")
    if not lines:
        return "状態情報はまだありません"
    return _bounded("現在の状態\n\n" + "\n".join(f"- {line}" for line in lines))


def render_chat_response(projection: Mapping[str, object]) -> str:
    """Render a read-only conversational response without echoing raw input."""

    if not isinstance(projection, Mapping):
        return "会話の参照情報を取得できませんでした。"
    text = projection.get("text")
    if not isinstance(text, str) or not text.strip():
        return "会話の参照情報を取得できませんでした。"
    return _bounded(_safe_text(text, maximum=1_800))


def render_final_response(text_segments: object) -> str:
    """Render a bounded natural-language completion projection."""

    if isinstance(text_segments, str):
        values = [text_segments]
    elif isinstance(text_segments, (list, tuple)):
        values = [item for item in text_segments if isinstance(item, str) and item.strip()]
    else:
        values = []
    text = "\n".join(_safe_text(item, maximum=800) for item in values).strip()
    return _bounded(text or "作業が完了しました。")


__all__ = ["render_chat_response", "render_echo", "render_final_response", "render_human_request", "render_ingress_ack", "render_progress", "render_read_projection"]
