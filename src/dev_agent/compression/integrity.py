"""Cheap, non-LLM information-retention checks for compressed payloads."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class RetentionWarning:
    kind: str
    value: str


_PATTERNS = {
    "date_or_number": re.compile(r"(?<!\w)(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d+(?:[.,]\d+)?%?)(?!\w)"),
    "url": re.compile(r"https?://[^\s)]+"),
    "commit_or_id": re.compile(r"(?<![A-Za-z0-9])[0-9a-f]{7,64}(?![A-Za-z0-9])", re.IGNORECASE),
    "path": re.compile(r"(?<!\w)(?:[A-Za-z]:[\\/]|(?:src|tests|docs|spec|scripts)[\\/])[^\s,;]+"),
    "negation": re.compile(r"\b(?:must\s+not|do\s+not|never|not)\b|禁止|不可|しない|ない" , re.IGNORECASE),
}


def inspect_information_retention(original: str, compressed: str) -> tuple[RetentionWarning, ...]:
    warnings: list[RetentionWarning] = []
    for kind, pattern in _PATTERNS.items():
        original_values = set(pattern.findall(original))
        compressed_values = set(pattern.findall(compressed))
        for value in sorted(original_values - compressed_values):
            warnings.append(RetentionWarning(kind=kind, value=value))
    return tuple(warnings)


__all__ = ["RetentionWarning", "inspect_information_retention"]
