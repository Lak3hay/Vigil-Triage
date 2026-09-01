"""Surge generation - the brief's 3x volume requirement.

    "Show how the system behaves under a simulated surge (e.g., 3x normal volume)."

Surge is not simply "more of the same patients". Two things change together, and
both matter for whether a triage assistant is any use:

* **Arrivals rise**, so more people are waiting at once and the re-assessment
  schedule stops being deliverable. That is the moment the system must escalate
  a staffing signal rather than silently accumulate overdue tasks.
* **Attention per patient falls**, which is precisely when under-triage gets
  worse. Crowding is documented to push acuity assignments *downward* - the
  system under-triages hardest exactly when accuracy matters most.

The second effect is modelled explicitly: under surge, simulated nurse acuity
is degraded for a fraction of arrivals. Without it a surge test only proves the
queue got longer, and misses the failure the queue is there to catch.

Generation is seeded and therefore reproducible: the same seed gives the same
shift, so a result can be re-derived rather than merely believed.
"""
from __future__ import annotations

import random
from dataclasses import replace
from datetime import timedelta

from vigil.sim.scenarios import SCENARIOS, Scenario, T0

#: Reference parameters from the brief: EDs of roughly 100 to 500+ visits/day.
#: 500/day is ~21 arrivals an hour; a 3x surge is ~63.
NORMAL_ARRIVALS_PER_HOUR = 21


def _jitter(value: float | None, pct: float, rng: random.Random) -> float | None:
    if value is None:
        return None
    return round(value * (1.0 + rng.uniform(-pct, pct)), 1)


def generate_surge(
    *,
    hours: float = 3.0,
    multiplier: float = 3.0,
    seed: int = 42,
    degrade_triage_under_load: bool = True,
) -> list[tuple[float, Scenario]]:
    """Generate a surge shift as ``(minutes_after_start, scenario)`` pairs.

    Patients are drawn from the hand-written cohort and perturbed, so every
    arrival remains a clinically coherent presentation rather than a random
    vector of numbers.

    Parameters
    ----------
    degrade_triage_under_load
        Simulate the documented crowding effect: as the department fills, a
        fraction of arrivals receive a *less urgent* nurse acuity than their
        physiology warrants. This is the failure the assistant exists to catch,
        so a surge test without it is not testing the interesting thing.
    """
    rng = random.Random(seed)
    per_hour = NORMAL_ARRIVALS_PER_HOUR * multiplier
    n = int(per_hour * hours)

    out: list[tuple[float, Scenario]] = []
    for i in range(n):
        base = SCENARIOS[rng.randrange(len(SCENARIOS))]
        s = base.snapshot
        minute = round(i * (hours * 60) / n, 1)

        # Crowding degrades triage accuracy, and it worsens through the shift.
        nurse_acuity = s.nurse_acuity
        degraded = False
        if degrade_triage_under_load and nurse_acuity is not None and nurse_acuity < 5:
            pressure = min(0.35, 0.05 + 0.30 * (minute / (hours * 60)))
            if rng.random() < pressure:
                nurse_acuity = min(5, nurse_acuity + 1)
                degraded = True

        snap = replace(
            s,
            patient_id=f"S{i:03d}",
            observed_at=T0 + timedelta(minutes=minute),
            hr=_jitter(s.hr, 0.08, rng),
            rr=_jitter(s.rr, 0.10, rng),
            spo2=None if s.spo2 is None else min(100.0, round(s.spo2 + rng.uniform(-2, 1), 1)),
            sbp=_jitter(s.sbp, 0.08, rng),
            dbp=_jitter(s.dbp, 0.08, rng),
            temp_c=None if s.temp_c is None else round(s.temp_c + rng.uniform(-0.3, 0.3), 1),
            nurse_acuity=nurse_acuity,
            tags=tuple(s.tags) + (("triage_degraded_by_crowding",) if degraded else ()),
        )
        out.append((minute, Scenario(
            snapshot=snap,
            why_included=f"surge arrival, perturbed from {base.id}"
                         + (" with crowding-degraded triage" if degraded else ""),
            expected=base.expected,
            follow_up=base.follow_up,
        )))
    return out


def surge_summary(arrivals: list[tuple[float, Scenario]]) -> dict:
    n = len(arrivals)
    degraded = sum(1 for _, s in arrivals
                   if "triage_degraded_by_crowding" in s.snapshot.tags)
    span_h = (max(m for m, _ in arrivals) - min(m for m, _ in arrivals)) / 60 or 1
    return {
        "arrivals": n,
        "hours": round(span_h, 2),
        "arrivals_per_hour": round(n / span_h, 1),
        "triage_degraded_by_crowding": degraded,
        "triage_degraded_pct": round(100 * degraded / n, 1),
    }
