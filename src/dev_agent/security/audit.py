"""Provider-neutral audit payload classification and size bounding."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


class AuditRecorder:
    MAX_STRING_CHARS = 4096
    MAX_PAYLOAD_BYTES = 32 * 1024
    RETENTION_SECONDS = 24 * 60 * 60
    SECRET_KEYS = frozenset({"token", "secret", "password", "api_key", "apikey", "authorization", "cookie", "private_key", "client_secret", "credential", "access_key", "refresh_token", "id_token", "session"})
    SECRET_PATTERNS = (
        re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
        re.compile(r"(?i)\b(?:api[_-]?key|secret|token|password)\s*[:=]\s*[^\s,;]+"),
        re.compile(r"\b(?:sk-(?:proj-)?|AIza|ghp_|github_pat_|AKIA)[A-Za-z0-9._-]{8,}\b"),
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    )

    @classmethod
    def sanitize_payload(cls, payload: dict[str, Any], *, artifact_store: Any | None = None) -> dict[str, Any]:
        def scrub(value: Any, key: str = "") -> Any:
            normalized_key = key.lower().replace("-", "_")
            if any(word in normalized_key for word in cls.SECRET_KEYS):
                return "[REDACTED]"
            if isinstance(value, dict):
                return {str(k): scrub(v, str(k)) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [scrub(v, key) for v in value]
            if isinstance(value, str):
                for pattern in cls.SECRET_PATTERNS:
                    value = pattern.sub("[REDACTED]", value)
                return value if len(value) <= cls.MAX_STRING_CHARS else value[: cls.MAX_STRING_CHARS] + "...[TRUNCATED]"
            if value is None or isinstance(value, (bool, int, float)):
                return value
            return f"[UNSERIALIZABLE:{type(value).__name__}]"

        safe = scrub(payload)
        encoded = json.dumps(safe, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) <= cls.MAX_PAYLOAD_BYTES:
            return safe
        if artifact_store is not None:
            artifact = artifact_store.put(encoded, content_type="application/json", retention_seconds=cls.RETENTION_SECONDS)
            reference, expires = artifact["uri"], artifact["expires_at"]
        else:
            reference, expires = f"event-sha256:{hashlib.sha256(encoded).hexdigest()}", None
        return {"_truncated": True, "payload_ref": reference, "byte_length": len(encoded), **({"artifact_expires_at": expires} if expires is not None else {})}
