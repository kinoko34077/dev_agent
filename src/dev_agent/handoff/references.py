"""Validated, reference-first payload metadata for model handoffs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from typing import Any, Mapping
from urllib.parse import urlsplit


_MAX_REFERENCE_BYTES = 10_000_000


def _text(value: Any, name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _timestamp(value: Any, name: str, *, optional: bool = False) -> str | None:
    normalized = _text(value, name, optional=optional)
    if normalized is None:
        return None
    candidate = normalized[:-1] + "+00:00" if normalized.endswith("Z") else normalized
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return normalized


@dataclass(frozen=True)
class ExternalTextReference:
    """A content-addressed external text location.

    This is metadata only.  It deliberately does not fetch or upload content,
    and it is never treated as an authority or control channel by itself.
    """

    location: str
    sha256: str
    size: int
    created_at: str
    expires_at: str | None = None
    source_label: str | None = None

    def __post_init__(self) -> None:
        location = _text(self.location, "location")
        assert location is not None
        parsed = urlsplit(location)
        if parsed.scheme.lower() != "https" or not parsed.netloc:
            raise ValueError("location must be an absolute https URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("location must not contain credentials")
        if parsed.query:
            raise ValueError("location must not contain a query")
        if parsed.fragment:
            raise ValueError("location must not contain a fragment")
        digest = _text(self.sha256, "sha256")
        assert digest is not None
        if len(digest) != hashlib.sha256().digest_size * 2 or any(char not in "0123456789abcdefABCDEF" for char in digest):
            raise ValueError("sha256 must be a SHA-256 hex digest")
        if isinstance(self.size, bool) or not isinstance(self.size, int) or not 0 < self.size <= _MAX_REFERENCE_BYTES:
            raise ValueError(f"size must be between 1 and {_MAX_REFERENCE_BYTES}")
        created_at = _timestamp(self.created_at, "created_at")
        expires_at = _timestamp(self.expires_at, "expires_at", optional=True)
        assert created_at is not None
        if expires_at is not None:
            created = datetime.fromisoformat(created_at[:-1] + "+00:00" if created_at.endswith("Z") else created_at)
            expires = datetime.fromisoformat(expires_at[:-1] + "+00:00" if expires_at.endswith("Z") else expires_at)
            if expires <= created:
                raise ValueError("expires_at must be later than created_at")
        source_label = _text(self.source_label, "source_label", optional=True)
        object.__setattr__(self, "location", location)
        object.__setattr__(self, "sha256", digest.lower())
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "source_label", source_label)

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "type": "external_text",
            "location": self.location,
            "sha256": self.sha256,
            "size": self.size,
            "created_at": self.created_at,
        }
        if self.expires_at is not None:
            value["expires_at"] = self.expires_at
        if self.source_label is not None:
            value["source_label"] = self.source_label
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExternalTextReference":
        if not isinstance(value, Mapping):
            raise TypeError("external text reference must be an object")
        if value.get("type") != "external_text":
            raise ValueError("external text reference type must be external_text")
        return cls(
            location=value.get("location"),
            sha256=value.get("sha256"),
            size=value.get("size"),
            created_at=value.get("created_at"),
            expires_at=value.get("expires_at"),
            source_label=value.get("source_label"),
        )


__all__ = ["ExternalTextReference"]
