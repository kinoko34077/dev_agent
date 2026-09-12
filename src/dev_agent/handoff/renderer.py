"""Human-readable renderers for structured handoffs."""

from __future__ import annotations

import json
from typing import Any

from .directive import AuthoritySource, ContinuationMode
from .protocol import HandoffEnvelope
from .validation import validate_handoff


def _sentence(value: str) -> str:
    value = value.rstrip()
    return value if value.endswith(("。", "！", "？")) else value + "。"


def _caution(value: str) -> str:
    value = value.rstrip("。")
    if value.endswith("に注意すること"):
        return value + "。"
    if not value.endswith("こと"):
        value += "こと"
    return value + "に注意すること。"


def _joined(values: tuple[str, ...], *, separator: str = "、") -> str:
    return separator.join(values)


def _authority_line(value: str) -> str:
    names = {
        AuthoritySource.CURRENT_USER_INSTRUCTION.value: "現在のユーザー明示指示",
        AuthoritySource.LATEST_USER_CORRECTION.value: "最新のユーザー訂正",
        AuthoritySource.CURRENT_REPOSITORY.value: "現行repository",
        AuthoritySource.CURRENT_PROJECT_SOURCE.value: "現行project source",
        AuthoritySource.PROVIDED_PAYLOAD.value: "提供されたPayload",
        AuthoritySource.PREVIOUS_FINDINGS.value: "前回findings",
        AuthoritySource.DOMAIN_SOURCE.value: "関連Domain Source",
        AuthoritySource.CORE_SOURCE.value: "Core Source",
    }
    return f"正本は{names.get(value, value)}として扱うこと。"


def _source_requirement_line(value: str) -> str:
    names = {
        "current_repository": "現行repositoryを実際に確認する",
        "current_project_source": "現行project sourceを確認する",
        "attached_material": "添付資料を確認する",
        "web_current": "Web上の現行情報を確認する",
        "previous_findings": "前回findingsを確認する",
        "roadmap": "ロードマップを確認する",
        "execution_evidence": "実行証跡を確認する",
        "provided_payload": "提供されたPayloadを確認する",
    }
    return _sentence(names.get(value, value))


def _continuation_line(envelope: HandoffEnvelope) -> str | None:
    directive = envelope.directive
    mode = directive.continuation_mode
    if mode == ContinuationMode.FRESH.value:
        return None
    if mode == ContinuationMode.CONTINUE.value:
        return "前段の確定事項を維持して処理を進めること。"
    if mode == ContinuationMode.RECHECK.value:
        return "現物を再取得して再確認すること。"
    if mode == ContinuationMode.REAUDIT_AFTER_CHANGE.value:
        return "前回findingsを現物と照合し、修正後に再監査すること。"
    if mode == ContinuationMode.REANALYZE_AFTER_CORRECTION.value:
        return f"最新訂正「{directive.latest_correction}」を正本とし、旧解釈を無効として再分析すること。"
    if mode == ContinuationMode.INTEGRATE_PREVIOUS.value:
        return "複数の既存結果を重複なく統合し、obsoleteな解釈を除外すること。"
    return None


def _comparison_line(envelope: HandoffEnvelope) -> str | None:
    directive = envelope.directive
    if not directive.comparison_targets:
        return None
    axes = f"を{_joined(directive.comparison_axes)}の観点で" if directive.comparison_axes else "を"
    return f"{_joined(directive.comparison_targets)}{axes}比較すること。"


def _output_lines(envelope: HandoffEnvelope) -> list[str]:
    contract = envelope.directive.output_contract
    lines: list[str] = []
    section_policy = contract.get("section_detail_policy")
    if isinstance(section_policy, dict):
        sections = {"successes": "正常部分", "issues": "問題点", "roadmap": "次ロードマップ"}
        details = {"brief": "簡潔に", "detailed": "詳細", "list": "一覧で"}
        parts = [
            f"{sections.get(key, key)}は{details.get(value, value)}"
            for key, value in section_policy.items()
            if isinstance(key, str) and isinstance(value, str)
        ]
        if parts:
            lines.append("、".join(parts) + "示すこと。")
    if isinstance(contract.get("format"), str):
        lines.append(f"出力形式は{contract['format']}とすること。")
    if isinstance(contract.get("detail_level"), str):
        lines.append(f"全体の詳細度は{contract['detail_level']}とすること。")
    if isinstance(contract.get("ordering"), str):
        lines.append(f"出力順は{contract['ordering']}とすること。")
    for field, label in (("include", "含める"), ("exclude", "出力から除外する")):
        value = contract.get(field)
        if isinstance(value, list) and all(isinstance(item, str) for item in value) and value:
            lines.append(f"{_joined(tuple(value))}を{label}こと。")
    return lines


def _payload_text(envelope: HandoffEnvelope) -> str:
    if envelope.payload is not None:
        if isinstance(envelope.payload, str):
            return envelope.payload
        return json.dumps(envelope.payload, ensure_ascii=False, sort_keys=True, indent=2)
    if envelope.payload_reference is not None:
        return json.dumps({"payload_reference": dict(envelope.payload_reference)}, ensure_ascii=False, sort_keys=True, indent=2)
    return ""


def render_handoff(value: HandoffEnvelope, *, renderer: str = "kinotch-ja-v1") -> str:
    envelope = validate_handoff(value)
    if renderer != "kinotch-ja-v1":
        raise ValueError(f"unsupported handoff renderer: {renderer}")
    lines = [
        f"以下、{_sentence(envelope.subject)}",
        _sentence(envelope.instruction),
    ]
    continuation = _continuation_line(envelope)
    if continuation:
        lines.append(continuation)
    if envelope.directive.authority_source:
        lines.append(_authority_line(envelope.directive.authority_source))
    lines.extend(_source_requirement_line(item) for item in envelope.directive.source_requirements)
    comparison = _comparison_line(envelope)
    if comparison:
        lines.append(comparison)
    if envelope.directive.focus:
        lines.append(f"特に、{_joined(envelope.directive.focus)}を詳しく扱うこと。")
    lines.extend(f"ただし、{_sentence(condition)}" for condition in envelope.conditions)
    lines.extend(_caution(caution) for caution in envelope.cautions)
    lines.extend(_sentence(requirement) for requirement in envelope.requirements)
    lines.extend(_sentence(exclusion) for exclusion in envelope.directive.exclusions)
    lines.extend(_output_lines(envelope))
    lines.extend(("┈┈┈┈┈┈┈┈┈┈", _payload_text(envelope)))
    return "\n".join(lines)


__all__ = ["render_handoff"]
