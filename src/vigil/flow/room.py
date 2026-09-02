"""The waiting room - a live queue, not a list of labels.

This module is the Round 2 brief's WATCH mandate, implemented:

    "The system must monitor patients already in the waiting queue and trigger
    re-assessment if wait time exceeds safe thresholds for their severity level
    **or** if vitals are re-recorded as worsening."

Both triggers, because they catch different failures. The clock catches the
patient nobody has looked at; the trend catches the patient somebody looked at
and reasonably called stable. A department that only had the first would still
miss the deteriorating patient who was re-checked on time.

Design commitments carried through from the engine:

* **Escalation only.** Every path through this module can raise urgency,
  shorten a clock or ask for a human. None can lower a level - that requires a
  clinician, and it is recorded as their decision.
* **Silence is not reassurance.** A patient who has not been re-checked does
  not become safer over time. The overdue clock is itself an escalating signal,
  not a passive report.
* **The clock adds no work; it re-orders existing work.** Reassessment
  intervals are already mandated by the triage standard in use. When capacity
  genuinely cannot meet the schedule, that surfaces as a staffing signal rather
  than accumulating silently as overdue tasks - otherwise a nurse manager
  correctly rejects the system on day one as "more alerts".
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum

from vigil.clinical.scores import MonitoringPlan
from vigil.flow.policy import HarmPolicy, RoutingDecision, route
from vigil.flow.watch import Observation, TrendSignal, detect_trend, normal_range_flags
from vigil.triage.confidence import ConfidenceLevel
from vigil.triage.engine import TriageAssessment, assess
from vigil.triage.snapshot import PatientSnapshot


class EventKind(str, Enum):
    ARRIVED = "arrived"
    REASSESSMENT_DUE = "reassessment_due"
    DETERIORATION = "deterioration_detected"
    ESCALATED = "escalated"
    TARGET_BREACHED = "target_breached"
    REVIEW_REQUIRED = "review_required"
    OBSERVATION = "observation_recorded"
    OVERRIDE = "clinician_override"
    SEEN = "seen_by_clinician"
    CAPACITY = "capacity_warning"


@dataclass(frozen=True)
class WatchEvent:
    """Something the waiting room noticed, and what it wants done."""

    at: datetime
    kind: EventKind
    patient_id: str
    detail: str
    severity: str = "info"           # info | attention | urgent
    action: str = ""                 # the concrete thing a human should do

    def to_dict(self) -> dict:
        return {
            "at": self.at.isoformat(), "kind": self.kind.value,
            "patient_id": self.patient_id, "detail": self.detail,
            "severity": self.severity, "action": self.action,
        }


@dataclass
class WaitingPatient:
    """One patient waiting, with everything observed about them so far."""

    snapshot: PatientSnapshot
    assessment: TriageAssessment
    arrived_at: datetime
    observations: list[Observation] = field(default_factory=list)
    last_assessed_at: datetime | None = None
    seen_at: datetime | None = None
    override_level: int | None = None
    override_reason: str = ""
    override_by: str = ""
    acknowledged_flags: set[str] = field(default_factory=set)
    trend: TrendSignal | None = None

    # ── state ─────────────────────────────────────────────────────────────────
    @property
    def patient_id(self) -> str:
        return self.snapshot.patient_id

    @property
    def effective_level(self) -> int:
        """The level actually in force.

        A clinician override wins over the recommendation - always, and in both
        directions. The system's job after that is to record it, not to argue.
        """
        return self.override_level if self.override_level is not None else self.assessment.recommended_level

    @property
    def is_waiting(self) -> bool:
        return self.seen_at is None

    def waited_minutes(self, now: datetime) -> float:
        end = self.seen_at or now
        return max(0.0, (end - self.arrived_at).total_seconds() / 60.0)

    def minutes_since_assessment(self, now: datetime) -> float:
        ref = self.last_assessed_at or self.arrived_at
        return max(0.0, (now - ref).total_seconds() / 60.0)

    def is_overdue(self, now: datetime) -> bool:
        return self.minutes_since_assessment(now) >= self.assessment.monitoring.interval_minutes

    @property
    def has_unresolved_flag(self) -> bool:
        return any(f.id not in self.acknowledged_flags for f in self.assessment.red_flags)

    @property
    def deteriorating(self) -> bool:
        return bool(self.trend and self.trend.worsening)

    def cost_of_waiting(self, policy: HarmPolicy, now: datetime) -> float:
        return policy.cost_of_waiting(
            self.effective_level,
            self.waited_minutes(now),
            deteriorating=self.deteriorating,
            unresolved_flag=self.has_unresolved_flag,
        )

    def routing(self) -> RoutingDecision:
        return route(
            level=self.effective_level,
            age_band_is_paediatric=self.snapshot.age_band.is_paediatric,
            red_flag_ids=tuple(f.id for f in self.assessment.red_flags),
        )


@dataclass
class WaitingRoom:
    """A live queue under one site's declared policy."""

    policy: HarmPolicy
    patients: dict[str, WaitingPatient] = field(default_factory=dict)
    events: list[WatchEvent] = field(default_factory=list)
    #: Clinician-minutes available per hour for reassessments. Used to decide
    #: when the schedule has become undeliverable and must be escalated as a
    #: staffing problem rather than hidden as overdue tasks.
    reassessment_capacity_per_hour: int = 12

    # ── arrivals ──────────────────────────────────────────────────────────────
    def admit(self, snapshot: PatientSnapshot, *, now: datetime | None = None) -> WaitingPatient:
        now = now or snapshot.observed_at
        a = assess(snapshot, now=now)
        wp = WaitingPatient(
            snapshot=snapshot, assessment=a, arrived_at=now, last_assessed_at=now,
            observations=[Observation(
                at=now, hr=snapshot.hr, rr=snapshot.rr, spo2=snapshot.spo2,
                sbp=snapshot.sbp, temp_c=snapshot.temp_c, score_total=a.score.total,
            )],
        )
        self.patients[wp.patient_id] = wp
        self._emit(now, EventKind.ARRIVED, wp.patient_id,
                   f"triaged level {a.recommended_level}; {a.headline}",
                   severity="attention" if a.recommended_level <= 2 else "info",
                   action=f"re-check within {a.monitoring.interval_minutes} min")
        if a.escalated:
            self._emit(now, EventKind.ESCALATED, wp.patient_id,
                       f"escalated from nurse level {a.nurse_acuity} to {a.recommended_level}",
                       severity="urgent", action="clinician to confirm escalation")
        if a.confidence.level is ConfidenceLevel.ABSTAIN:
            self._emit(now, EventKind.REVIEW_REQUIRED, wp.patient_id,
                       "insufficient information for an automated assessment",
                       severity="attention",
                       action=a.confidence.next_best_action or "clinician review")
        return wp

    # ── re-checks ─────────────────────────────────────────────────────────────
    def record_observation(
        self, patient_id: str, *, at: datetime, **vitals
    ) -> tuple[WaitingPatient, list[WatchEvent]]:
        """Record a repeat set of vitals and re-run the assessment.

        This is the second WATCH trigger: *vitals re-recorded as worsening*.
        """
        wp = self.patients[patient_id]
        new: list[WatchEvent] = []

        updated = wp.snapshot.with_vitals(**vitals)
        before = wp.assessment
        after = assess(updated, now=at)

        wp.observations.append(Observation(
            at=at, hr=updated.hr, rr=updated.rr, spo2=updated.spo2,
            sbp=updated.sbp, temp_c=updated.temp_c, score_total=after.score.total,
        ))
        wp.snapshot = updated
        wp.last_assessed_at = at

        # Trend across everything observed so far.
        flags = normal_range_flags(updated.age_band, wp.observations[-1])
        wp.trend = detect_trend(wp.observations, in_normal_range=flags)

        new.append(self._emit(at, EventKind.OBSERVATION, patient_id,
                              f"vitals re-recorded; score {before.score.total} -> {after.score.total}"))

        if wp.trend.worsening:
            new.append(self._emit(
                at, EventKind.DETERIORATION, patient_id,
                f"{wp.trend.headline}: {'; '.join(wp.trend.reasons)}",
                severity="urgent",
                action="reassess now" + (" - trend is invisible to threshold checks"
                                         if wp.trend.silent else ""),
            ))

        # Escalation only: a re-check can raise urgency, never lower it.
        merged_level = min(after.recommended_level, before.recommended_level)
        if merged_level < before.recommended_level:
            new.append(self._emit(
                at, EventKind.ESCALATED, patient_id,
                f"level {before.recommended_level} -> {merged_level} on re-assessment",
                severity="urgent", action="clinician to confirm escalation",
            ))
        # A deteriorating patient must not be handed a relaxed clock. The
        # monitoring interval comes from the score, and a score can still be
        # low while the *trend* is the whole finding -- so without this, we
        # detect deterioration and then say "come back in four hours".
        # (MISTAKES.md 2026-09-02.)
        monitoring = after.monitoring
        if wp.trend.worsening:
            monitoring = MonitoringPlan(
                interval_minutes=min(15, monitoring.interval_minutes),
                rationale=(f"{monitoring.rationale}; overridden to 15 min because the "
                           f"trend is worsening"),
                band="deteriorating",
            )

        wp.assessment = replace(
            after,
            recommended_level=merged_level,
            escalated=after.escalated or merged_level < (after.nurse_acuity or 5),
            monitoring=monitoring,
        )
        return wp, new

    # ── the clock ─────────────────────────────────────────────────────────────
    def tick(self, now: datetime) -> list[WatchEvent]:
        """Run the re-assessment clock over everyone still waiting.

        The first WATCH trigger: *wait time exceeds safe thresholds for their
        severity level*.
        """
        new: list[WatchEvent] = []
        overdue: list[WaitingPatient] = []

        for wp in self.waiting():
            if wp.is_overdue(now):
                overdue.append(wp)
                mins = wp.minutes_since_assessment(now)
                new.append(self._emit(
                    now, EventKind.REASSESSMENT_DUE, wp.patient_id,
                    f"re-check overdue by {mins - wp.assessment.monitoring.interval_minutes:.0f} min "
                    f"(interval {wp.assessment.monitoring.interval_minutes} min)",
                    severity="urgent" if wp.effective_level <= 2 else "attention",
                    action="repeat observations",
                ))
            breach = self.policy.minutes_to_breach(wp.effective_level, wp.waited_minutes(now))
            if breach < 0:
                new.append(self._emit(
                    now, EventKind.TARGET_BREACHED, wp.patient_id,
                    f"past time-to-be-seen target by {abs(breach):.0f} min",
                    severity="attention", action="prioritise or escalate to charge nurse",
                ))

        # Capacity: if the schedule cannot be delivered, say so out loud rather
        # than letting overdue tasks pile up invisibly.
        if len(overdue) > self.reassessment_capacity_per_hour:
            new.append(self._emit(
                now, EventKind.CAPACITY, "-",
                f"{len(overdue)} re-checks overdue against capacity of "
                f"{self.reassessment_capacity_per_hour}/hour - the reassessment "
                f"schedule is not deliverable at current staffing",
                severity="urgent", action="charge nurse: staffing escalation",
            ))
        return new

    # ── clinician actions ─────────────────────────────────────────────────────
    def override(
        self, patient_id: str, *, new_level: int, by: str, reason: str, at: datetime
    ) -> WatchEvent:
        """Record a clinician overriding the recommendation.

        The clinician always wins, including downward. Our job is to record it
        completely enough to be reviewable later - who, when, from what, to
        what, and why - because that is what clinical accountability requires
        and what the assumed jurisdiction expects of an automated decision that
        a human has altered.
        """
        wp = self.patients[patient_id]
        was = wp.effective_level
        wp.override_level, wp.override_by, wp.override_reason = new_level, by, reason
        direction = "escalated" if new_level < was else "de-escalated" if new_level > was else "confirmed"
        return self._emit(
            at, EventKind.OVERRIDE, patient_id,
            f"{by} {direction} level {was} -> {new_level}: {reason}",
            severity="attention",
            action="recorded in the audit log; recommendation superseded",
        )

    def acknowledge_flag(self, patient_id: str, flag_id: str) -> None:
        """A clinician has looked at a red flag. It stops inflating the queue."""
        self.patients[patient_id].acknowledged_flags.add(flag_id)

    def mark_seen(self, patient_id: str, *, at: datetime) -> WatchEvent:
        wp = self.patients[patient_id]
        wp.seen_at = at
        return self._emit(at, EventKind.SEEN, patient_id,
                          f"seen after {wp.waited_minutes(at):.0f} min")

    # ── views ─────────────────────────────────────────────────────────────────
    def waiting(self) -> list[WaitingPatient]:
        return [p for p in self.patients.values() if p.is_waiting]

    def ranked(self, now: datetime) -> list[tuple[WaitingPatient, float]]:
        """Everyone still waiting, most costly-to-delay first.

        This is the sequencing decision. It is autonomous because its worst
        case is that somebody is seen slightly out of order - wasted effort,
        never missed care - and because no acuity level is changed by it.
        """
        rows = [(p, p.cost_of_waiting(self.policy, now)) for p in self.waiting()]
        return sorted(rows, key=lambda r: (-r[1], r[0].arrived_at))

    def next_patient(self, now: datetime) -> WaitingPatient | None:
        r = self.ranked(now)
        return r[0][0] if r else None

    def _emit(self, at, kind, pid, detail, severity="info", action="") -> WatchEvent:
        ev = WatchEvent(at=at, kind=kind, patient_id=pid, detail=detail,
                        severity=severity, action=action)
        self.events.append(ev)
        return ev
