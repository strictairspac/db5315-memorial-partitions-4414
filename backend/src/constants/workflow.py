from enum import StrEnum


class Role(StrEnum):
    OPERATOR = 'operator'
    REVIEWER = 'reviewer'
    VIEWER = 'viewer'


class Status(StrEnum):
    QUEUED = 'queued'
    RUNNING = 'running'
    COMPLETE = 'complete'
    FAILED = 'failed'


class PlanStatus(StrEnum):
    # Preview recorded, not yet applied. Only one preview per (case, plan_key) is kept.
    PREVIEWED = 'previewed'
    # Preview is no longer valid: source payload or the last signed evidence changed.
    STALE = 'stale'
    # Apply accepted and queued/running; the previous version stays authoritative until commit.
    APPLYING = 'applying'
    # Mount, detach and per-slip reconciliation committed; signed evidence exists.
    APPLIED = 'applied'
    # Apply failed and the previous version was kept unchanged.
    FAILED = 'failed'
