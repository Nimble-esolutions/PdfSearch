"""Bounded, evidence-first automatic recovery decisions.

The reconciler decides what is safe to queue; it does not activate runtimes or
repair source data. The existing maintenance worker remains the executor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping


SAFE_ACTIONS = frozenset({"recover_orphan_jobs", "refresh_observation", "repair_indexes", "reindex_documents", "cleanup_temp_workspace"})
APPROVAL_ACTIONS = frozenset({"restore_candidate", "activate_staging", "activate_production"})


@dataclass
class AutoHealBudget:
    per_run: int
    per_day: int
    used_run: int = 0
    used_day: int = 0

    def remaining(self) -> int:
        return max(0, min(self.per_run - self.used_run, self.per_day - self.used_day))

    def take(self, requested: int) -> int:
        amount = max(0, min(int(requested), self.remaining()))
        self.used_run += amount
        self.used_day += amount
        return amount


@dataclass(frozen=True)
class RecoveryDecision:
    action: str
    reason: str
    count: int = 0
    requires_approval: bool = False
    evidence: Mapping[str, object] = field(default_factory=dict)


def choose_repairs(observations: Iterable[Mapping[str, object]], budget: AutoHealBudget) -> tuple[RecoveryDecision, ...]:
    """Convert fresh observations into bounded, non-destructive actions."""
    decisions: list[RecoveryDecision] = []
    for observation in observations:
        condition = str(observation.get("condition", "")).strip()
        if condition in SAFE_ACTIONS:
            requested = int(observation.get("count", 1) or 1)
            count = budget.take(requested)
            if count:
                decisions.append(RecoveryDecision(condition, "bounded_safe_repair", count, evidence={"observation_id": observation.get("id", "")}))
            elif requested:
                decisions.append(RecoveryDecision(condition, "budget_exhausted", 0, evidence={"observation_id": observation.get("id", "")}))
        elif condition in APPROVAL_ACTIONS:
            decisions.append(RecoveryDecision(condition, "operator_approval_required", int(observation.get("count", 1) or 1), True, {"observation_id": observation.get("id", "")}))
        elif condition:
            decisions.append(RecoveryDecision(condition, "unknown_condition_stopped", 0, evidence={"observation_id": observation.get("id", "")}))
    return tuple(decisions)
