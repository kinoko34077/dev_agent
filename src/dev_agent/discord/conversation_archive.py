"""Verified JSONL.gz archival and read-only search for conversation messages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from .conversation_log import ConversationLog, ConversationMessage


@dataclass(frozen=True)
class ArchiveManifest:
    archive_file: str
    count: int
    sha256: str
    created_at: str


def _fsync_file(path: Path) -> None:
    # Windows rejects fsync on a read-only descriptor (Errno 9).
    # Re-open the already-written file in append mode without changing it.
    with path.open("ab") as handle:
        os.fsync(handle.fileno())


def _readback_archive(path: Path, *, expected_count: int, expected_sha256: str) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise OSError("archive sha256 read-back mismatch")
    count = 0
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            json.loads(line)
            count += 1
    if count != expected_count:
        raise OSError("archive record count read-back mismatch")


def archive_eligible_messages(
    log: ConversationLog,
    archive_root: str | Path,
    *,
    now: datetime | None = None,
    max_age_days: int = 30,
    per_channel_limit: int = 2_000,
) -> ArchiveManifest:
    if not isinstance(log, ConversationLog):
        raise TypeError("log must be a ConversationLog")
    if isinstance(max_age_days, bool) or not isinstance(max_age_days, int) or max_age_days < 1:
        raise ValueError("max_age_days must be positive")
    if isinstance(per_channel_limit, bool) or not isinstance(per_channel_limit, int) or per_channel_limit < 1:
        raise ValueError("per_channel_limit must be positive")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = (current - timedelta(days=max_age_days)).isoformat()
    messages = list(log.list_archive_candidates(cutoff=cutoff, per_channel_limit=per_channel_limit))
    root = Path(archive_root)
    root.mkdir(parents=True, exist_ok=True)
    created_at = current.isoformat()
    if not messages:
        return ArchiveManifest(archive_file="", count=0, sha256="", created_at=created_at)

    temp_name = f"conversation-{uuid4().hex}.jsonl.gz.tmp"
    temp_path = root / temp_name
    try:
        with temp_path.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb") as compressed:
                for message in messages:
                    line = json.dumps(message.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
                    compressed.write(line.encode("utf-8"))
            raw.flush()
            os.fsync(raw.fileno())
        digest = hashlib.sha256(temp_path.read_bytes()).hexdigest()
        archive_file = f"conversation-{current.strftime('%Y%m%dT%H%M%SZ')}-{digest[:12]}.jsonl.gz"
        archive_path = root / archive_file
        manifest_path = root / f"{archive_file}.manifest.json"
        os.replace(temp_path, archive_path)
        manifest = ArchiveManifest(archive_file=archive_file, count=len(messages), sha256=digest, created_at=created_at)
        manifest_temp = root / f"{archive_file}.manifest.tmp"
        _readback_archive(archive_path, expected_count=len(messages), expected_sha256=digest)
        manifest_temp.write_text(json.dumps(manifest.__dict__, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        _fsync_file(manifest_temp)
        os.replace(manifest_temp, manifest_path)
        log.delete([message.message_id for message in messages])
        return manifest
    except BaseException:
        if temp_path.exists():
            temp_path.unlink()
        if 'archive_path' in locals() and archive_path.exists():
            archive_path.unlink()
        if 'manifest_path' in locals() and manifest_path.exists():
            manifest_path.unlink()
        raise


def search_archive(
    archive_root: str | Path,
    binding_key: str,
    query: str,
    *,
    limit: int = 50,
) -> tuple[ConversationMessage, ...]:
    if not isinstance(binding_key, str) or not binding_key.strip():
        raise ValueError("binding_key must be non-empty text")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be non-empty text")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    result: list[ConversationMessage] = []
    for manifest_path in sorted(Path(archive_root).glob("*.jsonl.gz.manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        archive_path = manifest_path.parent / manifest["archive_file"]
        if not archive_path.is_file():
            continue
        with gzip.open(archive_path, "rt", encoding="utf-8") as handle:
            for line in handle:
                value: dict[str, Any] = json.loads(line)
                if value.get("binding_key") != binding_key or query.casefold() not in str(value.get("content", "")).casefold():
                    continue
                result.append(ConversationMessage(**value))
                if len(result) >= limit:
                    return tuple(result)
    return tuple(result)


__all__ = ["ArchiveManifest", "archive_eligible_messages", "search_archive"]
