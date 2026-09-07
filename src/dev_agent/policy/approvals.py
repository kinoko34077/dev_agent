"""Side-effect approval policy; approval is explicit data, never model choice."""

from __future__ import annotations


class ApprovalPolicy:
    DEFAULT_REQUIRED = frozenset({"external_write", "financial", "credential", "destructive"})

    def __init__(self, required_levels: set[str] | None = None) -> None:
        self.required_levels = frozenset(required_levels or self.DEFAULT_REQUIRED)

    def requires_approval(self, side_effect_level: str) -> bool:
        return side_effect_level in self.required_levels

    def authorize(self, side_effect_level: str, *, approved: bool = False) -> bool:
        return approved if self.requires_approval(side_effect_level) else True
