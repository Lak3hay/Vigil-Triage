"""Operating mode - how the department behaves when it is beyond capacity.

The Round 2 brief asks teams to *"show how the system behaves under a simulated
surge"* and, separately, how it *"behaves differently during a surge versus a
quiet shift"*. Generating more arrivals and counting more events does not answer
either question: that is the same system with a longer queue.

## What surge actually changes

Under normal load the department can, in principle, deliver every re-check the
triage standard mandates. Under surge it cannot, and pretending otherwise
produces two specific failures:

* **A schedule nobody can meet.** Overdue tasks accumulate silently, the board
  fills with red, and staff learn that red means nothing. A target that cannot
  be hit is not a safety standard - it is an alarm generator.
* **Attention spread evenly over people who need it unevenly.** Which is the
  same thing as taking it away from the sickest.

So surge mode does not relax safety. It **concentrates** it: the re-check
schedule is itself triaged, low-acuity targets stretch to something achievable,
and the alert threshold rises so the interrupts that remain are the ones worth
interrupting for.

## What surge must never change

Three invariants hold in every mode, and each is tested:

1. **A deteriorating patient's clock never stretches.** Ever.
2. **Level 1 and 2 clocks never stretch.**
3. **The safety property is untouched** - surge cannot cause a de-escalation,
   because nothing in this module touches acuity at all.

Surge mode reallocates *attention*. It has no authority over *acuity*, which
keeps it inside the same rule as everything else the system decides alone: its
worst case is wasted effort, never missed care.

Entering and leaving surge is an **audited event**, not an internal flag. A
department that cannot see when the assistant changed its own behaviour cannot
review it afterwards.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OperatingMode(str, Enum):
    NORMAL = "normal"
    SURGE = "surge"


@dataclass(frozen=True)
class SurgeRules:
    """When surge begins, and what it changes.

    Attributes
    ----------
    enter_waiting, leave_waiting
        Queue length that triggers entry and exit. Deliberately different:
        without hysteresis the mode oscillates around the boundary, and a
        system that flips its own behaviour every few minutes is worse than one
        that never changes it at all.
    enter_demand_ratio
        The real trigger. Surge is not "many patients" - it is **the schedule
        you owe exceeding the schedule you can deliver**. Demand is the sum of
        ``60 / interval`` over everyone waiting; capacity is the department's
        re-checks per hour. Above this ratio the schedule is undeliverable, and
        continuing to issue it produces overdue tasks nobody can clear.

        Queue length alone misreads both directions: a long queue being kept up
        with is not a surge, and a short queue of unstable patients is.
    alert_floor
        Minimum severity that interrupts a clinician. Everything below is
        batched for the charge nurse rather than discarded.
    low_acuity_target_multiplier
        Stretches level 4-5 time-to-be-seen targets to something achievable.
    stretch_low_risk_recheck
        Low-risk re-check intervals lengthen. High-risk intervals are left
        exactly as they are.

        **Surge does not tighten anything, and that is deliberate.** An earlier
        version halved high-risk intervals to "concentrate attention", which in
        a room full of sick patients raised total demand from 40 to 57
        re-checks an hour - inventing capacity that does not exist and making
        the schedule less deliverable, not more. Under surge there are fewer
        clinician-minutes, not more. Concentration comes from stretching
        everyone else, which is real, rather than from promising the sickest a
        cadence nobody can staff.
    """

    enter_waiting: int = 25
    leave_waiting: int = 18
    enter_demand_ratio: float = 1.25
    alert_floor: str = "urgent"
    low_acuity_target_multiplier: float = 2.0
    stretch_low_risk_recheck: float = 2.0

    def __post_init__(self) -> None:
        if self.enter_demand_ratio <= 1.0:
            raise ValueError(
                "enter_demand_ratio must exceed 1: at or below 1 the schedule is "
                "still deliverable, and declaring surge would relax targets a "
                "department could actually have met."
            )
        if self.leave_waiting >= self.enter_waiting:
            raise ValueError(
                "leave_waiting must be below enter_waiting: without hysteresis the "
                "mode oscillates around the boundary and the department sees the "
                "assistant change its own behaviour every few minutes."
            )
        if self.stretch_low_risk_recheck < 1.0:
            raise ValueError("stretch_low_risk_recheck must be >= 1")



DEFAULT_SURGE_RULES = SurgeRules()

#: Severity ordering, so "at least this urgent" is a comparison rather than a
#: chain of if-statements.
_SEVERITY = {"info": 0, "attention": 1, "urgent": 2}


def recheck_demand_per_hour(intervals: list[int]) -> float:
    """Re-checks per hour the current schedule demands.

    The quantity that decides whether a department is in surge: what the
    reassessment standard obliges, expressed in the same unit as staffing.
    """
    return round(sum(60.0 / max(i, 1) for i in intervals), 1)


def should_enter_surge(
    *, waiting: int, demand_per_hour: float, capacity_per_hour: float,
    mode: OperatingMode, rules: SurgeRules
) -> bool:
    """Whether the schedule the department owes exceeds what it can deliver."""
    if mode is OperatingMode.SURGE:
        return waiting > rules.leave_waiting          # hysteresis on the way out
    ratio = demand_per_hour / max(capacity_per_hour, 1e-9)
    return waiting >= rules.enter_waiting and ratio >= rules.enter_demand_ratio


def interrupts(severity: str, mode: OperatingMode, rules: SurgeRules) -> bool:
    """Whether an event of this severity should interrupt a clinician now.

    Below the floor an event is not discarded - it is batched for the charge
    nurse. The distinction matters: suppressing information and re-routing it
    are different things, and only one of them is defensible.
    """
    if mode is OperatingMode.NORMAL:
        return _SEVERITY.get(severity, 0) >= _SEVERITY["attention"]
    return _SEVERITY.get(severity, 0) >= _SEVERITY[rules.alert_floor]


def adjust_recheck_interval(
    minutes: int,
    *,
    level: int,
    deteriorating: bool,
    mode: OperatingMode,
    rules: SurgeRules,
) -> tuple[int, str]:
    """Triage the re-check schedule itself. Returns ``(minutes, reason)``.

    The guard is the point of this function: a deteriorating patient, or anyone
    at level 1-2, is never stretched. Their interval is left untouched - surge
    does not economise on them, and it does not pretend to give them more
    either, because there are fewer clinician-minutes under surge, not more.
    """
    if mode is OperatingMode.NORMAL:
        return minutes, ""

    if deteriorating or level <= 2:
        return minutes, ("surge: high-risk interval held - the sickest keep their "
                         "cadence while everyone else's is stretched")

    stretched = int(minutes * rules.stretch_low_risk_recheck)
    return stretched, ("surge: low-risk re-check stretched to a deliverable "
                       "interval - a schedule nobody can meet teaches staff to "
                       "ignore it")


def adjust_target(minutes: float, *, level: int, mode: OperatingMode,
                  rules: SurgeRules) -> float:
    """Stretch low-acuity time-to-be-seen targets under surge.

    Level 1-3 targets are clinical commitments and do not move. Level 4-5
    targets are service commitments, and holding a service commitment the
    department demonstrably cannot meet only fills the board with breaches
    nobody can act on.
    """
    if mode is OperatingMode.NORMAL or level <= 3:
        return minutes
    return minutes * rules.low_acuity_target_multiplier
