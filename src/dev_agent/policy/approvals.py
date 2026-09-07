"""Side-effect approval policy; approval is explicit data, never model choice."""

from __future__ import annotations

import hashlib
import json


def canonical_arguments_hash(arguments: dict) -> str:
    payload = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ApprovalPolicy:
    DEFAULT_REQUIRED = frozenset({"external_write", "financial", "credential", "destructive"})

    def __init__(self, required_levels: set[str] | None = None) -> None:
        self.required_levels = frozenset(required_levels or self.DEFAULT_REQUIRED)

    def requires_approval(self, side_effect_level: str) -> bool:
        return side_effect_level in self.required_levels

    def authorize(self, side_effect_level: str, *, approved: bool = False, approval_id: str | None = None, task_id: str | None = None, call_id: str | None = None, arguments_hash: str | None = None, store=None) -> bool:
        if not self.requires_approval(side_effect_level):
            return True
        if approval_id and task_id and call_id and arguments_hash and store is not None:
            return store.has_approval(approval_id, task_id=task_id, side_effect_level=side_effect_level, call_id=call_id, arguments_hash=arguments_hash)
        return approved
