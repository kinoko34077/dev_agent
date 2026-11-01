"""Host-owned materialization of bounded line-anchored Worker edits."""

from __future__ import annotations

from dataclasses import dataclass
import difflib
from collections.abc import Callable, Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from scripts.devfarm_errors import DevFarmError


MAX_LOCALIZED_EDITS = 8
MAX_LOCALIZED_LINE_CHARS = 16_000
MAX_LOCALIZED_PATCH_CHARS = 512 * 1024
GitObjectReader = Callable[[str, str], bytes]


def _path(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 512:
        raise DevFarmError(f"{name} must be bounded text")
    normalized = value.strip().replace("\\", "/")
    parsed = PurePosixPath(normalized)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise DevFarmError(f"{name} must be a safe relative path")
    return parsed.as_posix()


def _line_number(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > 1_000_000:
        raise DevFarmError(f"{name} must be a positive bounded integer")
    return value


def _lines(value: Any, name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise DevFarmError(f"{name} must be a line list")
    if not allow_empty and not value:
        raise DevFarmError(f"{name} must not be empty")
    if len(value) > 100_000:
        raise DevFarmError(f"{name} contains too many lines")
    result: list[str] = []
    for line in value:
        if not isinstance(line, str) or "\r" in line or "\n" in line:
            raise DevFarmError(f"{name} must contain complete source lines")
        if len(line) > MAX_LOCALIZED_LINE_CHARS:
            raise DevFarmError(f"{name} contains an oversized line")
        result.append(line)
    return tuple(result)


@dataclass(frozen=True)
class LocalizedEditMaterialization:
    patch: str
    changed_files: tuple[str, ...]
    edit_count: int
    line_ranges: tuple[tuple[str, int, int], ...]


def _validate_edit(value: Mapping[str, Any]) -> tuple[str, int, int, tuple[str, ...], tuple[str, ...]]:
    allowed = {"path", "start_line", "end_line", "expected_lines", "replacement_lines"}
    unknown = set(value) - allowed
    if unknown:
        raise DevFarmError(f"localized edit has unknown field: {sorted(unknown)[0]}")
    path = _path(value.get("path"), "localized edit path")
    start = _line_number(value.get("start_line"), "localized edit start_line")
    end = _line_number(value.get("end_line"), "localized edit end_line")
    if end < start:
        raise DevFarmError("localized edit end_line must not precede start_line")
    expected = _lines(value.get("expected_lines"), "localized edit expected_lines")
    replacement = _lines(value.get("replacement_lines"), "localized edit replacement_lines")
    if len(expected) != end - start + 1:
        raise DevFarmError("localized edit expected_lines length does not match line range")
    return path, start, end, expected, replacement


def _new_file_text(base: bytes, edits: Sequence[tuple[int, int, tuple[str, ...], tuple[str, ...]]]) -> str:
    try:
        source = base.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DevFarmError("localized edit base file is not UTF-8") from exc
    if "\x00" in source:
        raise DevFarmError("localized edit base file contains NUL")
    had_final_newline = source.endswith(("\n", "\r"))
    lines = source.splitlines()
    for start, end, expected, replacement in sorted(edits, key=lambda item: item[0], reverse=True):
        actual = tuple(lines[start - 1 : end])
        if actual != expected:
            raise DevFarmError("localized edit expected_lines do not match the pinned base revision")
        lines[start - 1 : end] = list(replacement)
    result = "\n".join(lines)
    if had_final_newline:
        result += "\n"
    return result


def materialize_localized_edits(
    manifest: Mapping[str, Any],
    edits: Sequence[Mapping[str, Any]],
    *,
    read_file_at_revision: GitObjectReader,
) -> LocalizedEditMaterialization:
    """Validate exact line anchors and create a Host-owned unified diff."""

    if not isinstance(manifest, Mapping):
        raise DevFarmError("manifest must be an object")
    if not callable(read_file_at_revision):
        raise DevFarmError("read_file_at_revision must be callable")
    if isinstance(edits, (str, bytes)) or not isinstance(edits, Sequence) or not edits:
        raise DevFarmError("localized edits must be a non-empty list")
    if len(edits) > MAX_LOCALIZED_EDITS:
        raise DevFarmError(f"localized edits exceed {MAX_LOCALIZED_EDITS}")
    base_revision = manifest.get("base_revision")
    if not isinstance(base_revision, str) or not base_revision.strip():
        raise DevFarmError("manifest base_revision is required")
    outbound = {_path(item, "manifest outbound_files[]") for item in manifest.get("outbound_files", ())}
    new_files = {_path(item, "manifest new_files[]") for item in manifest.get("new_files", ())}
    if not outbound:
        raise DevFarmError("localized edits require outbound_files")

    parsed: list[tuple[str, int, int, tuple[str, ...], tuple[str, ...]]] = []
    for raw in edits:
        if not isinstance(raw, Mapping):
            raise DevFarmError("localized edits must contain objects")
        item = _validate_edit(raw)
        path, start, end, expected, replacement = item
        if path not in outbound:
            raise DevFarmError("localized edit path must be within outbound_files")
        if path in new_files:
            raise DevFarmError("localized edits cannot target new_files")
        parsed.append(item)

    grouped: dict[str, list[tuple[int, int, tuple[str, ...], tuple[str, ...]]]] = {}
    for path, start, end, expected, replacement in parsed:
        grouped.setdefault(path, []).append((start, end, expected, replacement))
    for path, items in grouped.items():
        ordered = sorted(items, key=lambda item: item[0])
        for previous, current in zip(ordered, ordered[1:]):
            if current[0] <= previous[1]:
                raise DevFarmError(f"localized edits overlap for {path}")

    patches: list[str] = []
    line_ranges: list[tuple[str, int, int]] = []
    for path in sorted(grouped):
        base = read_file_at_revision(base_revision.strip(), path)
        if not isinstance(base, bytes):
            raise DevFarmError("localized edit reader must return bytes")
        try:
            original = base.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DevFarmError("localized edit base file is not UTF-8") from exc
        updated = _new_file_text(base, grouped[path])
        if updated == original:
            raise DevFarmError("localized edits do not change the pinned base file")
        body = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                n=3,
            )
        )
        if not body:
            raise DevFarmError("localized edit produced no diff")
        patches.append(f"diff --git a/{path} b/{path}\n{body}")
        line_ranges.extend((path, start, end) for start, end, _expected, _replacement in sorted(grouped[path]))
    patch = "".join(patches)
    if len(patch) > MAX_LOCALIZED_PATCH_CHARS:
        raise DevFarmError("localized edit patch exceeds the bounded output limit")
    return LocalizedEditMaterialization(
        patch=patch,
        changed_files=tuple(sorted(grouped)),
        edit_count=len(parsed),
        line_ranges=tuple(line_ranges),
    )


__all__ = [
    "LocalizedEditMaterialization",
    "MAX_LOCALIZED_EDITS",
    "materialize_localized_edits",
]
