"""The triage engine - where the safety property lives.

Composition order, and the reason for it:

1. Resolve the **age band**, and from it the instrument (NEWS2 or PEWS-style).
2. Compute the physiological **urgency floor** from that score.
3. Run the encoded **red-flag panel** for the presentations a score cannot see.
4. Decompose **confidence**, and let uncertainty tighten observation.
5. Compose - **and never come out less urgent than the human did.**

Step 5 is the whole design. The brief requires it and requires it to be shown:

    "Under-triage and over-triage carry asymmetric costs - missing a critical
    case is categorically worse than over-prioritizing a minor one. Any
    solution must be deliberately tuned to bias toward escalation under
    uncertainty rather than optimized for average accuracy, and teams must
    demonstrate this design choice explicitly in their prototype."

So we do not merely claim it. ``recommended_level <= nurse_acuity`` is a
property enforced here by construction and tested exhaustively over every
scenario in ``tests/test_safety_property.py``. Adding Vigil to a department
**cannot create a new under-triage failure that did not already exist without
it** - it can only ever raise urgency, tighten a clock, or ask for another
look. Its worst case is wasted effort, never missed care.

The engine is deterministic and side-effect free: same snapshot in, same
assessment out, no network, no model server, no language model. Explanations
are generated from the rule trace, not written by an LLM. That is a deliberate
position - a score a regulator cannot reproduce is not usable in a clinical
workflow, and the language model is confined to describing what the
deterministic layer already decided (README, "What the LLM does not do").
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from vigil.clinical.agebands import AgeBand
from vigil.clinical.redflags import RedFlag, escalation_floor, evaluate
from vigil.clinical.scores import (
    MonitoringPlan,
    ScoreResult,
    early_warning_score,
    monitoring_interval,
)
from vigil.triage.confidence import Confidence, ConfidenceLevel, assess_confidence
from vigil.triage.snapshot import PatientSnapshot

#: Acuity is 1 (immediate) to 5 (non-urgent). Lower is more urgent, so
#: "escalate" means "take the minimum". Stated once; relied on everywhere.
MOST_URGENT, LEAST_URGENT = 1, 5

ENGINE_VERSION = "vigil-triage/0.2.0"


@dataclass(frozen=True)
class Decision:
    """One thing the system did, and the authority under which it did it.

    ``authority`` is the Round 1 ladder, enforced in code rather than described
    in a slide:

    * ``decides``    - autonomous. Worst case is wasted effort.
    * ``recommends`` - requires a human to accept.
    * ``never``      - out of scope by design; recorded so the boundary is visible.
    """

    authority: str
    action: str
    detail: str


@dataclass(frozen=True)
class TriageAssessment:
    """Everything the engine concluded, with its full derivation.

    Nothing here is a bare number: every field that could drive a clinical
    action carries the reasoning that produced it.
    """

    patient_id: str
    assessed_at: datetime
    age_band: AgeBand

    score: ScoreResult
    red_flags: tuple[RedFlag, ...]
    confidence: Confidence
    monitoring: MonitoringPlan

    computed_level: int | None      # what physiology + rules say on their own
    nurse_acuity: int | None        # the human's independent call, committed first
    recommended_level: int          # what we surface -- never less urgent than the nurse
    escalated: bool                 # did we raise urgency above the human's call?

    decisions: tuple[Decision, ...] = ()
    rationale: tuple[str, ...] = ()
    engine_version: str = ENGINE_VERSION

    # ── properties used by the board and the audit log ────────────────────────
    @property
    def requires_human_review(self) -> bool:
        """Abstention, or an escalation a human has not yet accepted."""
        return self.confidence.level is ConfidenceLevel.ABSTAIN or self.escalated

    @property
    def disagrees_with_nurse(self) -> bool:
        return (
            self.nurse_acuity is not None
            and self.computed_level is not None
            and self.computed_level != self.nurse_acuity
        )

    @property
    def headline(self) -> str:
        if self.confidence.level is ConfidenceLevel.ABSTAIN:
            return "Insufficient information - clinician review required"
        if self.red_flags:
            return self.red_flags[0].name
        if self.escalated:
            return f"Escalated to level {self.recommended_level}"
        return f"Consistent with level {self.recommended_level}"

    @property
    def one_line(self) -> str:
        """The whole assessment in one sentence a clinician can read in two seconds.

        The brief requires decisions to be "explainable within seconds, by a
        clinician who is often simultaneously managing several other patients".
        A paragraph of rationale is not that, however correct it is - it is
        something to read later. This is the version that has to survive being
        glanced at over a shoulder, so it names the level, the single strongest
        reason, and the confidence, and nothing else.
        """
        if self.confidence.level is ConfidenceLevel.ABSTAIN:
            need = self.confidence.next_best_action or "clinician review"
            return f"Not enough to assess - {need}"
        if self.red_flags:
            f = self.red_flags[0]
            verb = f"Level {self.recommended_level}"
            if self.escalated:
                verb = f"Escalate {self.nurse_acuity} -> {self.recommended_level}"
            return f"{verb}: {f.name.lower()} ({self.confidence.level.value} confidence)"
        if self.escalated:
            return (f"Escalate {self.nurse_acuity} -> {self.recommended_level}: "
                    f"{self.score.instrument} {self.score.total} "
                    f"({self.confidence.level.value} confidence)")
        return (f"Level {self.recommended_level} confirmed, {self.score.instrument} "
                f"{self.score.total}, re-check {self.monitoring.interval_minutes} min")

    def to_dict(self) -> dict:
        """Flat, JSON-safe form for the audit log and the board."""
        return {
            "patient_id": self.patient_id,
            "assessed_at": self.assessed_at.isoformat(),
            "engine_version": self.engine_version,
            "age_band": self.age_band.value,
            "instrument": self.score.instrument,
            "score_total": self.score.total,
            "score_components": dict(self.score.components),
            "score_missing": list(self.score.missing),
            "red_flags": [
                {"id": f.id, "name": f.name, "escalate_to": f.escalate_to,
                 "rationale": f.rationale, "why_missed": f.why_missed}
                for f in self.red_flags
            ],
            "confidence_level": self.confidence.level.value,
            "confidence_score": self.confidence.score,
            "confidence_factors": [
                {"name": c.name, "score": c.score, "detail": c.detail, "remedy": c.remedy}
                for c in self.confidence.factors
            ],
            "next_best_action": self.confidence.next_best_action,
            "reassess_within_minutes": self.monitoring.interval_minutes,
            "monitoring_rationale": self.monitoring.rationale,
            "computed_level": self.computed_level,
            "nurse_acuity": self.nurse_acuity,
            "recommended_level": self.recommended_level,
            "escalated": self.escalated,
            "requires_human_review": self.requires_human_review,
            "decisions": [
                {"authority": d.authority, "action": d.action, "detail": d.detail}
                for d in self.decisions
            ],
            "one_line": self.one_line,
            "rationale": list(self.rationale),
        }


def urgency_floor_from_score(score: ScoreResult) -> int | None:
    """Map an early-warning score to the most urgent level it justifies.

    ``None`` means *physiology gives no reason to escalate* - which is not the
    same as "this patient is fine". Levels 3-5 in a five-level scale turn on
    predicted resource need, which vitals cannot tell us, so the engine
    deliberately declines to guess there and defers to the nurse. Claiming a
    level we cannot derive would be exactly the over-reach the design forbids.
    """
    if score.total >= 7 or score.has_red_parameter:
        return 1
    if score.total >= 5:
        return 2
    if score.total >= 3:
        return 3
    return None


def assess(
    snapshot: PatientSnapshot,
    *,
    minutes_since_observation: float = 0.0,
    now: datetime | None = None,
) -> TriageAssessment:
    """Assess one patient. Deterministic, offline, side-effect free."""
    band = snapshot.age_band
    now = now or snapshot.observed_at

    # 1-2. Age-appropriate instrument, then the urgency it justifies.
    score = early_warning_score(
        band,
        hr=snapshot.hr, rr=snapshot.rr, spo2=snapshot.spo2, sbp=snapshot.sbp,
        temp_c=snapshot.temp_c, consciousness=snapshot.consciousness,
        on_oxygen=snapshot.on_oxygen, work_of_breathing=snapshot.work_of_breathing,
        age_years=snapshot.age_years,
    )
    score_floor = urgency_floor_from_score(score)

    # 3. The presentations a score cannot see.
    flags = evaluate(snapshot)
    flag_floor = escalation_floor(flags)

    computed = min([f for f in (score_floor, flag_floor) if f is not None], default=None)

    # 4. Uncertainty. It tightens observation; it never relaxes anything.
    monitoring = monitoring_interval(score, band)
    confidence = assess_confidence(
        snapshot, score,
        minutes_since_observation=minutes_since_observation,
        expected_interval_minutes=monitoring.interval_minutes,
    )
    if confidence.level is ConfidenceLevel.ABSTAIN:
        # A patient with nothing recorded scores 0, which reads as "well" and
        # would otherwise earn the LONGEST re-check interval -- exactly backwards.
        # An unscored patient is the one we know least about, so ignorance caps
        # the clock rather than relaxing it (MISTAKES.md, 2026-09-01).
        monitoring = MonitoringPlan(
            interval_minutes=min(30, monitoring.interval_minutes),
            rationale=(f"{monitoring.rationale}; capped at 30 min because the score is "
                       f"not interpretable ({len(score.missing)} parameters unrecorded)"),
            band="abstain",
        )
    elif confidence.level is ConfidenceLevel.LOW:
        monitoring = MonitoringPlan(
            interval_minutes=max(15, monitoring.interval_minutes // 2),
            rationale=f"{monitoring.rationale}; halved because confidence is low",
            band=monitoring.band,
        )

    # 5. Compose. This is the safety property, and it is a `min`.
    nurse = snapshot.nurse_acuity
    candidates = [v for v in (nurse, computed) if v is not None]
    recommended = min(candidates) if candidates else LEAST_URGENT
    recommended = max(MOST_URGENT, min(LEAST_URGENT, recommended))
    escalated = nurse is not None and recommended < nurse

    rationale = _build_rationale(snapshot, score, flags, confidence, monitoring,
                                 computed, nurse, recommended, escalated)
    decisions = _build_decisions(monitoring, confidence, flags, recommended, escalated)

    return TriageAssessment(
        patient_id=snapshot.patient_id,
        assessed_at=now,
        age_band=band,
        score=score,
        red_flags=flags,
        confidence=confidence,
        monitoring=monitoring,
        computed_level=computed,
        nurse_acuity=nurse,
        recommended_level=recommended,
        escalated=escalated,
        decisions=decisions,
        rationale=rationale,
    )


def _build_rationale(snapshot, score, flags, confidence, monitoring,
                     computed, nurse, recommended, escalated) -> tuple[str, ...]:
    """Deterministic explanation, generated from the trace. No language model."""
    out: list[str] = []
    band = snapshot.age_band
    out.append(
        f"Age band {band.label}; {score.instrument} = {score.total}"
        + (f" (single parameter scoring {score.max_single})" if score.has_red_parameter else "")
        + (f"; not scored: {', '.join(score.missing)}" if score.missing else "")
    )
    if band.is_paediatric:
        out.append(
            "Paediatric thresholds applied - adult vital-sign ranges would read "
            "this patient as more stable than they are."
        )
    for f in flags:
        out.append(f"RED FLAG - {f.name}: {f.rationale}")
    if snapshot.shock_index is not None and snapshot.shock_index >= 0.9:
        out.append(
            f"Shock index {snapshot.shock_index} (HR/SBP >= 0.9) - suggests occult "
            "hypoperfusion while both components may still read individually normal."
        )
    if snapshot.sbp_drop_from_baseline and snapshot.sbp_drop_from_baseline >= 40:
        out.append(
            f"Systolic pressure is {snapshot.sbp_drop_from_baseline:.0f} points below "
            "this patient's own baseline."
        )
    if not snapshot.has_prior_record:
        out.append("No prior health record available - assessment rests on observation alone.")
    out.append(
        f"Confidence {confidence.level.value} ({confidence.score:.2f})"
        + (f"; limited by {confidence.weakest.name}" if confidence.weakest and confidence.weakest.is_weak else "")
    )
    if confidence.next_best_action:
        out.append(f"Most useful next step: {confidence.next_best_action}.")
    if escalated:
        out.append(
            f"Escalated from nurse level {nurse} to {recommended}. Escalation is "
            "automatic; de-escalation always requires a clinician."
        )
    elif nurse is not None and computed is None:
        out.append(
            f"Nothing in the physiology or the rule panel justifies escalating above "
            f"the nurse's level {nurse}. We defer - the system never lowers an acuity level."
        )
    elif nurse is not None and computed is not None and computed > nurse:
        out.append(
            f"Our own reading was level {computed}, less urgent than the nurse's {nurse}. "
            "We defer to the nurse - the system never lowers an acuity level."
        )
    out.append(f"Re-assess within {monitoring.interval_minutes} min - {monitoring.rationale}.")
    return tuple(out)


def _build_decisions(monitoring, confidence, flags, recommended, escalated) -> tuple[Decision, ...]:
    """The authority ladder, emitted per assessment so the boundary is auditable."""
    d = [
        Decision("decides", "reassessment_interval",
                 f"re-check within {monitoring.interval_minutes} min ({monitoring.rationale})"),
        Decision("decides", "observation_intensity",
                 f"monitoring band '{monitoring.band}'; confidence {confidence.level.value}"),
        Decision("recommends", "acuity_level",
                 f"level {recommended}" + (" (escalated)" if escalated else "")),
    ]
    if flags:
        d.append(Decision("recommends", "pathway_activation",
                          "; ".join(f.name for f in flags)))
    if confidence.level is ConfidenceLevel.ABSTAIN:
        d.append(Decision("recommends", "clinician_review",
                          "abstained - insufficient information for an automated view"))
    d.append(Decision("never", "de_escalation",
                      "the system cannot lower an acuity level; only a clinician can"))
    return tuple(d)
