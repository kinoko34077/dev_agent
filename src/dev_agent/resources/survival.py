"""Deterministic resource-exhaustion operating modes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SurvivalMode(str, Enum):
    NORMAL = "NORMAL"
    CONSERVE = "CONSERVE"
    SURVIVAL = "SURVIVAL"


@dataclass(frozen=True)
class SurvivalSnapshot:
    normal_remaining_minor: int
    recovery_remaining_minor: int
    healthy_resources: int
    queued_tasks: int = 0


@dataclass(frozen=True)
class SurvivalState:
    mode: SurvivalMode
    reasons: tuple[str, ...]


class SurvivalGovernor:
    def __init__(self, *, conserve_threshold_minor: int = 25) -> None:
        if conserve_threshold_minor < 0:
            raise ValueError("conserve threshold must be non-negative")
        self.conserve_threshold_minor = conserve_threshold_minor

    def evaluate(self, snapshot: SurvivalSnapshot) -> SurvivalState:
        if snapshot.normal_remaining_minor <= 0 or snapshot.healthy_resources <= 0:
            reasons = tuple(filter(None, ("normal_budget_exhausted" if snapshot.normal_remaining_minor <= 0 else "no_healthy_resources",)))
            return SurvivalState(SurvivalMode.SURVIVAL, reasons)
        if snapshot.normal_remaining_minor <= self.conserve_threshold_minor:
            return SurvivalState(SurvivalMode.CONSERVE, ("normal_budget_low",))
        return SurvivalState(SurvivalMode.NORMAL, ())

