"""Where Vigil meets the systems a hospital already runs.

The Round 2 brief:

    "Integration with existing hospital systems (patient records, bed
    management, staff rosters) is rarely simple, and system maturity varies a
    great deal from one hospital to the next."

**None of these adapters are implemented, and that is the honest state.** What
this module provides is the *boundary*: the exact question Vigil would ask each
system, the exact answer it needs back, and what it does when the answer never
comes. Publishing the interface without the implementation is more useful than
a stub that pretends - a hospital's integration team can read this file and say
"we can supply that in a fortnight" or "we cannot supply that at all", which is
the conversation that actually determines whether a deployment happens.

## The design rule these all share

**Every integration is optional, and every one degrades to something safe.**
Vigil never blocks on an external system. A record lookup that times out
produces a lower-confidence assessment, not a spinner; a bed-management system
that is offline means routing becomes advisory-only, not that triage stops.
This is the same principle as the rest of the system: a missing input is a
known unknown that lowers confidence, never a silent default.

## Capability tiers, not a single integration story

Hospitals differ in technical maturity as much as in size, so Vigil is designed
to run at four levels and to **say which level it is running at**:

======  ==================================  ===========================================
Tier    Available                           What Vigil does
======  ==================================  ===========================================
**T0**  Nothing. Paper department.          Vitals typed at a tablet. Full clinical
                                            engine, no history, no routing.
**T1**  Read-only patient record            Adds masking medications, prior visits,
                                            baseline vitals. The biggest single jump
                                            in assessment quality.
**T2**  T1 + bed / area state               Routing becomes concrete rather than
                                            advisory.
**T3**  T2 + staff roster                   The capacity signal becomes real:
                                            surge is measured against actual staffing
                                            rather than a configured constant.
======  ==================================  ===========================================

Most of the value lands at T1, which is deliberate. A system whose benefit
requires T3 is a system almost nobody can deploy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class IntegrationUnavailable(RuntimeError):
    """Raised by an unimplemented adapter, naming what it would need.

    Deliberately not a silent ``None``. An integration that fails quietly is
    indistinguishable from a patient with no history, and those two states must
    never be confused: one lowers confidence honestly, the other lowers it for
    a reason the clinician cannot see.
    """


@dataclass(frozen=True)
class Tier:
    level: str
    requires: str
    unlocks: str


TIERS: tuple[Tier, ...] = (
    Tier("T0", "nothing - vitals typed at a tablet",
         "full clinical engine, WATCH loop, audit trail"),
    Tier("T1", "read-only patient record lookup by health ID",
         "masking medications, prior visits, baseline vitals, bounce-back detection"),
    Tier("T2", "T1 + live bed and area occupancy",
         "routing becomes concrete rather than advisory"),
    Tier("T3", "T2 + staff roster",
         "surge measured against real staffing rather than a configured constant"),
)


@runtime_checkable
class PatientRecordSource(Protocol):
    """Tier 1. The single highest-value integration.

    Vigil asks one question - *what do you already know about this person that
    changes how I should read their vitals?* - and needs a small, specific
    answer: current medications (beta-blockers, immunosuppressants,
    anticoagulants), active chronic conditions, ED attendances in the last 90
    days with discharge times, and a baseline blood pressure.

    Note what is **not** requested: notes, letters, imaging, full history. Data
    minimisation is a legal obligation under the assumed jurisdiction, and a
    narrow request is also the one an integration team can actually satisfy.
    """

    def lookup(self, health_id: str, *, timeout_s: float = 2.0) -> dict: ...


@runtime_checkable
class BedManagementSource(Protocol):
    """Tier 2. Turns a routing recommendation into a concrete destination.

    Vigil needs occupancy and blocked-bed counts per area. It does not need,
    and must never be given, authority to *assign* a bed: moving a patient to
    the wrong area can delay care, so its worst case is not wasted effort and
    it therefore sits outside what the system may decide alone.
    """

    def area_state(self, *, timeout_s: float = 2.0) -> dict: ...


@runtime_checkable
class StaffRosterSource(Protocol):
    """Tier 3. Makes the capacity signal real.

    ``reassessment_capacity_per_hour`` is currently a configured constant. With
    a roster it becomes a measured quantity, and the surge trigger - demand
    exceeding capacity - stops depending on a number somebody guessed.
    """

    def on_shift(self, *, timeout_s: float = 2.0) -> dict: ...


# ── the honest implementations ────────────────────────────────────────────────

class NotIntegrated:
    """The default for every adapter: absent, and loudly so.

    Vigil runs at Tier 0 with this in place. Nothing breaks; assessments simply
    carry lower confidence on the ``completeness`` factor, and the clinician
    sees "no prior health record available" in the rationale rather than
    wondering whether the lookup silently failed.
    """

    def __init__(self, system: str, needed: str):
        self.system, self.needed = system, needed

    def _refuse(self):
        raise IntegrationUnavailable(
            f"{self.system} is not integrated. To enable it, supply: {self.needed}. "
            f"Vigil continues without it at reduced confidence - it never blocks on "
            f"an external system."
        )

    def lookup(self, health_id: str, *, timeout_s: float = 2.0) -> dict:
        self._refuse()

    def area_state(self, *, timeout_s: float = 2.0) -> dict:
        self._refuse()

    def on_shift(self, *, timeout_s: float = 2.0) -> dict:
        self._refuse()


DEFAULT_RECORD_SOURCE = NotIntegrated(
    "Patient record (ABDM/ABHA or hospital EMR)",
    "current medications, active chronic conditions, ED attendances in the last "
    "90 days with discharge times, and a baseline systolic blood pressure",
)
DEFAULT_BED_SOURCE = NotIntegrated(
    "Bed management",
    "per-area occupied / free / blocked counts, refreshed at least every 5 minutes",
)
DEFAULT_ROSTER_SOURCE = NotIntegrated(
    "Staff roster",
    "clinicians on shift by role for the current and next hour",
)


def current_tier(
    *, record: object | None = None, beds: object | None = None,
    roster: object | None = None,
) -> Tier:
    """Which tier is actually available right now.

    Surfaced to the clinician rather than kept internal. Trust must be
    calibrated to actual capability: a user who does not know the record
    lookup is down cannot know why the assessment is less confident than
    yesterday's.
    """
    real = lambda x: x is not None and not isinstance(x, NotIntegrated)  # noqa: E731
    if real(record) and real(beds) and real(roster):
        return TIERS[3]
    if real(record) and real(beds):
        return TIERS[2]
    if real(record):
        return TIERS[1]
    return TIERS[0]
