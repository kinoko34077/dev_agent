"""Thin Discord approval input boundary.

The adapter authenticates the Discord identity and delegates the decision to
the existing approval authority. It never consumes, creates, or interprets an
approval record itself.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..coordination.protocol_helpers import validate_identifier
from .auth import DiscordAuthorizer


class DiscordApprovalAdapter:
    """Forward an authorized button decision to an injected Core callback."""

    def __init__(self, *, authorizer: DiscordAuthorizer, submit: Callable[[str, bool, str], Any]) -> None:
        if not isinstance(authorizer, DiscordAuthorizer):
            raise TypeError("authorizer must be DiscordAuthorizer")
        if not callable(submit):
            raise TypeError("submit must be callable")
        self._authorizer = authorizer
        self._submit = submit

    def submit(
        self,
        approval_id: str,
        *,
        author_id: str,
        approved: bool,
        guild_id: str = "",
        channel_id: str = "",
    ) -> Any:
        if not isinstance(approved, bool):
            raise ValueError("approved must be boolean")
        authorized = (
            self._authorizer.is_user_allowed(author_id)
            if not guild_id and not channel_id
            else self._authorizer.is_allowed(author_id, guild_id, channel_id)
        )
        if not authorized:
            raise PermissionError("Discord identity is not authorized for approval")
        return self._submit(
            validate_identifier(approval_id, "approval_id"),
            approved,
            f"discord:{author_id}",
        )


__all__ = ["DiscordApprovalAdapter"]
