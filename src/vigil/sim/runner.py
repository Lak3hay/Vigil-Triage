"""A shift simulator, and the counterfactual that turns a demo into an experiment.

The original problem statement asks for a tool that reduces waiting times
*without replacing clinical judgment*. Safety arguments alone do not answer the
first half, so this module runs the same shift twice - identical arrivals,
identical deteriorations, identical service capacity - and changes exactly one
thing:

* **``fifo``**  - the status quo. Patients are seen in acuity order, and
  first-come-first-served within a band. Nobody is re-ranked after triage.
* **``vigil``** - the same queue ordered by cost of waiting, with the WATCH
  loop re-ranking patients whose vitals worsen.

Everything else is held constant, so any difference is attributable to the
ordering policy rather than to luck. That is the whole point: a demo shows the
system doing something, a counterfactual shows what difference it made.

**What this is and is not.** It is a simulation under stated assumptions, on
synthetic patients, with a service model that is deliberately simple. It is
*not* clinical evidence, and no number produced here should be quoted as one.
What it does support is a design claim - that re-ordering by cost of waiting
reaches deteriorating patients sooner without lengthening the queue overall -
and that claim is falsifiable by re-running with different assumptions, which
is why the assumptions are parameters rather than constants.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from vigil.audit import AuditLog, record_assessment
from vigil.flow import WaitingRoom
from vigil.flow.policy import HarmPolicy, URBAN_TRAUMA_CENTRE
from vigil.flow.room import EventKind
from vigil.sim.scenarios import SCENARIOS, Scenario, T0


@dataclass
class PatientOutcome:
    """What happened to one patient during the shift."""

    patient_id: str
    arrived_minute: float
    triage_level: int
    seen_minute: float | None = None
    deteriorated_at: float | None = None      # when their vitals actually worsened
    detected_at: float | None = None          # when the system noticed
    escalated: bool = False

    @property
    def waited(self) -> float | None:
        return None if self.seen_minute is None else self.seen_minute - self.arrived_minute

    @property
    def detection_lead(self) -> float | None:
        """Minutes between the system noticing and the patient being seen.

        Positive means the warning arrived *before* someone got to them, which
        is the only kind of warning that can change anything.
        """
        if self.detected_at is None or self.seen_minute is None:
            return None
        return round(self.seen_minute - self.detected_at, 1)

    @property
    def seen_after_deterioration(self) -> float | None:
        if self.deteriorated_at is None or self.seen_minute is None:
            return None
        return round(self.seen_minute - self.deteriorated_at, 1)


@dataclass
class ShiftResult:
    ordering: str
    outcomes: list[PatientOutcome] = field(default_factory=list)
    events: list = field(default_factory=list)
    audit: AuditLog = field(default_factory=AuditLog)

    @property
    def deteriorating(self) -> list[PatientOutcome]:
        return [o for o in self.outcomes if o.deteriorated_at is not None]

    def summary(self) -> dict:
        seen = [o for o in self.outcomes if o.seen_minute is not None]
        waits = [o.waited for o in seen if o.waited is not None]
        det = [o for o in self.deteriorating if o.seen_after_deterioration is not None]
        det_waits = [o.seen_after_deterioration for o in det]
        leads = [o.detection_lead for o in self.deteriorating if o.detection_lead is not None]
        return {
            "ordering": self.ordering,
            "patients": len(self.outcomes),
            "seen": len(seen),
            "median_wait_min": _median(waits),
            "mean_wait_min": round(sum(waits) / len(waits), 1) if waits else None,
            "deteriorating_patients": len(self.deteriorating),
            "median_minutes_from_deterioration_to_being_seen": _median(det_waits),
            "worst_minutes_from_deterioration_to_being_seen": max(det_waits) if det_waits else None,
            "median_detection_lead_min": _median(leads),
            "escalations": sum(1 for o in self.outcomes if o.escalated),
        }


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return round(s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2, 1)


def run_shift(
    arrivals: list[tuple[float, Scenario]],
    *,
    ordering: str = "vigil",
    policy: HarmPolicy = URBAN_TRAUMA_CENTRE,
    minutes_per_patient: float = 12.0,
    shift_minutes: float = 300.0,
    tick_minutes: float = 5.0,
) -> ShiftResult:
    """Run one shift under one ordering policy.

    Parameters
    ----------
    ordering
        ``"vigil"`` ranks by cost of waiting with WATCH re-ranking.
        ``"fifo"`` ranks by acuity then arrival time and never re-ranks.
    minutes_per_patient
        Service capacity: one clinician slot every N minutes. Held identical
        across arms, because the claim is about ordering, not throughput.
    """
    if ordering not in ("vigil", "fifo"):
        raise ValueError(f"unknown ordering {ordering!r}")

    room = WaitingRoom(policy=policy)
    result = ShiftResult(ordering=ordering)
    outcomes: dict[str, PatientOutcome] = {}
    pending = sorted(arrivals, key=lambda a: a[0])
    follow_ups: list[tuple[float, str, dict]] = []
    next_slot = 0.0

    t = 0.0
    while t <= shift_minutes:
        now = T0 + timedelta(minutes=t)

        # arrivals due
        while pending and pending[0][0] <= t:
            minute, scen = pending.pop(0)
            wp = room.admit(scen.snapshot, now=now)
            record_assessment(result.audit, wp.assessment)
            outcomes[scen.id] = PatientOutcome(
                patient_id=scen.id, arrived_minute=minute,
                triage_level=wp.assessment.recommended_level,
                escalated=wp.assessment.escalated,
            )
            for offset, vitals in scen.follow_up:
                follow_ups.append((minute + offset, scen.id, vitals))

        # scheduled re-checks (the deteriorations actually happening)
        due = [f for f in follow_ups if f[0] <= t]
        for f in due:
            follow_ups.remove(f)
            _, pid, vitals = f
            if pid not in room.patients or not room.patients[pid].is_waiting:
                continue
            o = outcomes[pid]
            if o.deteriorated_at is None:
                o.deteriorated_at = f[0]
            wp, evs = room.record_observation(pid, at=now, **vitals)
            # FIFO represents the status quo: the observation is charted, but
            # nothing re-ranks the queue as a result.
            if ordering == "vigil":
                for e in evs:
                    if e.kind is EventKind.DETERIORATION and o.detected_at is None:
                        o.detected_at = t

        result.events.extend(room.tick(now))

        # service: one slot every `minutes_per_patient`
        while next_slot <= t:
            waiting = room.waiting()
            if not waiting:
                break
            if ordering == "vigil":
                nxt = room.ranked(now)[0][0]
            else:
                nxt = sorted(waiting, key=lambda p: (p.assessment.recommended_level,
                                                     p.arrived_at))[0]
            room.mark_seen(nxt.patient_id, at=now)
            outcomes[nxt.patient_id].seen_minute = t
            next_slot += minutes_per_patient

        t += tick_minutes

    result.outcomes = list(outcomes.values())
    return result


def compare(
    arrivals: list[tuple[float, Scenario]] | None = None, **kw
) -> dict:
    """Run both arms on identical inputs and report the difference.

    Everything except the ordering policy is held constant, so the delta is
    attributable to the policy.
    """
    if arrivals is None:
        # A congested shift by default, because an empty waiting room has no
        # sequencing problem to solve. Arrivals must outpace service or both
        # orderings trivially agree -- which is the condition the whole system
        # exists for.
        from vigil.sim.surge import generate_surge
        arrivals = generate_surge(hours=4.0, multiplier=3.0, seed=42)
        kw.setdefault("minutes_per_patient", 4.0)
        kw.setdefault("shift_minutes", 420.0)

    fifo = run_shift(arrivals, ordering="fifo", **kw)
    vigil = run_shift(arrivals, ordering="vigil", **kw)
    a, b = fifo.summary(), vigil.summary()

    # PAIRED comparison. The two arms do not deteriorate the same set of
    # patients -- a patient seen before their trajectory fires never
    # deteriorates in that arm -- so comparing arm-level medians silently
    # compares different denominators. That is the "never let a filter gate a
    # measurement it could bias" failure, and it would flatter whichever arm
    # happened to leave more sick patients waiting. So we compare only patients
    # who deteriorated in BOTH arms, matched by id.
    f_by_id = {o.patient_id: o for o in fifo.deteriorating
               if o.seen_after_deterioration is not None}
    v_by_id = {o.patient_id: o for o in vigil.deteriorating
               if o.seen_after_deterioration is not None}
    common = sorted(set(f_by_id) & set(v_by_id))
    paired = [(f_by_id[i].seen_after_deterioration,
               v_by_id[i].seen_after_deterioration) for i in common]
    diffs = [v - f for f, v in paired]
    improved = sum(1 for d in diffs if d < 0)
    worsened = sum(1 for d in diffs if d > 0)

    def _delta(key: str) -> float | None:
        if a.get(key) is None or b.get(key) is None:
            return None
        return round(b[key] - a[key], 1)

    return {
        "fifo": a,
        "vigil": b,
        "paired_on_deteriorating_patients": {
            "n_matched": len(common),
            "n_deteriorated_fifo_only": len(set(f_by_id) - set(v_by_id)),
            "n_deteriorated_vigil_only": len(set(v_by_id) - set(f_by_id)),
            "median_minutes_deterioration_to_seen_fifo": _median([f for f, _ in paired]),
            "median_minutes_deterioration_to_seen_vigil": _median([v for _, v in paired]),
            "median_change_min": _median(diffs),
            "reached_sooner": improved,
            "reached_later": worsened,
            "unchanged": len(diffs) - improved - worsened,
        },
        "delta": {
            "median_wait_min": _delta("median_wait_min"),
            "median_minutes_from_deterioration_to_being_seen":
                _delta("median_minutes_from_deterioration_to_being_seen"),
            "worst_minutes_from_deterioration_to_being_seen":
                _delta("worst_minutes_from_deterioration_to_being_seen"),
        },
        "assumptions": {
            "service_capacity_minutes_per_patient": kw.get("minutes_per_patient", 12.0),
            "shift_minutes": kw.get("shift_minutes", 300.0),
            "policy": kw.get("policy", URBAN_TRAUMA_CENTRE).name,
            "note": "Simulation on synthetic patients under stated assumptions. "
                    "Not clinical evidence. Re-run with different parameters to test "
                    "whether the direction of the effect survives.",
        },
    }
