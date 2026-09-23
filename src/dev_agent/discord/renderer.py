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
    return _bounded(f"受信: {_safe_text(content, maximum=1_900)}")


def render_progress(stage: str, *, detail: str | None = None) -> str:
    label = _STAGE_LABELS.get(stage.casefold(), "作業状態が更新されました")
    if detail:
        return _bounded(f"{label}\n{_safe_text(detail)}")
    return label


def render_human_request(request: HumanRequest) -> str:
    if not isinstance(request, HumanRequest):
        raise TypeError("request must be HumanRequest")
    answers = " / ".join(_safe_text(answer, maximum=120) for answer in request.allowed_answers)
    suffix = f"\n選択肢: {answers}" if answers else ""
    rendered = (
        "判断が必要です\n"
        f"Request: {_safe_text(request.request_id, maximum=120)}\n"
        f"理由: {_safe_text(request.reason)}\n"
        f"質問: {_safe_text(request.question)}"
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
    return _bounded("\n".join(lines) or "状態情報はまだありません")


__all__ = ["render_echo", "render_human_request", "render_progress", "render_read_projection"]
