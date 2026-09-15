"""Shared exception type for development-only DevFarm boundaries."""

from __future__ import annotations


class DevFarmError(ValueError):
    """A development-farm manifest or result is unsafe or malformed."""


__all__ = ["DevFarmError"]
