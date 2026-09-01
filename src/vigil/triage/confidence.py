"""Uncertainty, decomposed.

The brief makes this non-negotiable:

    "Surface uncertainty explicitly - the prototype must not return a score
    without a confidence indicator."

A single opaque 0-1 number would satisfy the letter and miss the point. A
clinician deciding whether to trust an assessment needs to know *which kind* of
doubt they are looking at, because the remedies differ: missing vitals are
fixed by taking vitals, a stale reading is fixed by re-checking, an unknown age
is fixed by asking. So confidence decomposes into four named factors, each
carrying a concrete "what would raise this" action.

The rule that links uncertainty to safety, and the reason this module is not
merely cosmetic:

    **Low confidence escalates. It never de-escalates, and it never silently
    downgrades a recommendation.**

Under-triage and over-triage have asymmetric costs, so doubt must resolve
toward looking again - never toward reassurance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from vigil.clinical.agebands import AgeBand
from vigil.clinical.scores import ScoreResult
from vigil.triage.snapshot import VITAL_FIELDS, PatientSnapshot


class ConfidenceLevel(str, Enum):
    """Four states, in descending order of trust."""

    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"
    ABSTAIN = "abstain"        # too little to say anything; a human must look

    @property
    def rank(self) -> int:
        return {"high": 3, "moderate": 2, "low": 1, "abstain": 0}[self.value]


@dataclass(frozen=True)
class ConfidenceFactor:
    """One named contributor to confidence."""

    name: str
    score: float                # 0-1, higher is better
    detail: str
    remedy: str = ""            # the concrete action that would raise it

    @property
    def is_weak(self) -> bool:
        return self.score < 0.6


@dataclass(frozen=True)
class Confidence:
    """A confidence level with its full derivation."""

    level: ConfidenceLevel
    score: float
    factors: tuple[ConfidenceFactor, ...] = ()
    drivers: tuple[str, ...] = field(default_factory=tuple)

    @property
    def weakest(self) -> ConfidenceFactor | None:
        return min(self.factors, key=lambda f: f.score, default=None)

    @property
    def next_best_action(self) -> str:
        """The single action that would most improve this assessment.

        The Round 1 idea of asking "the one question that would most change my
        mind", reduced to something computable without an LLM.
        """
        w = self.weakest
        return w.remedy if (w and w.is_weak and w.remedy) else ""


#: Vitals without which any early-warning score is largely uninformative.
_CRITICAL_VITALS = ("hr", "rr", "spo2", "sbp")


def assess_confidence(
    snapshot: PatientSnapshot,
    score: ScoreResult,
    *,
    minutes_since_observation: float = 0.0,
    expected_interval_minutes: float = 60.0,
) -> Confidence:
    """Decompose confidence in an assessment of ``snapshot``.

    Parameters
    ----------
    minutes_since_observation
        Age of the most recent vitals. Staleness is not a detail: in this
        dataset the last observation at a waiting-room landmark is a median of
        ~1 hour old, and a value from three hours ago is not the same evidence
        as the same value from three minutes ago.
    """
    factors: list[ConfidenceFactor] = []

    # 1. Completeness -- how much of what we need do we actually have?
    present = [f for f in VITAL_FIELDS if getattr(snapshot, f) is not None]
    crit_missing = [f for f in _CRITICAL_VITALS if getattr(snapshot, f) is None]
    completeness = len(present) / len(VITAL_FIELDS)
    if crit_missing:
        completeness *= 0.5
    factors.append(ConfidenceFactor(
        name="completeness",
        score=round(completeness, 3),
        detail=(f"{len(present)}/{len(VITAL_FIELDS)} vitals recorded"
                + (f"; missing critical: {', '.join(crit_missing)}" if crit_missing else "")),
        remedy=f"record {', '.join(crit_missing)}" if crit_missing else "",
    ))

    # 2. Recency -- decays to 0 at twice the expected re-check interval.
    horizon = max(expected_interval_minutes * 2.0, 1.0)
    recency = max(0.0, 1.0 - (minutes_since_observation / horizon))
    factors.append(ConfidenceFactor(
        name="recency",
        score=round(recency, 3),
        detail=(f"last observation {minutes_since_observation:.0f} min ago; "
                f"expected interval {expected_interval_minutes:.0f} min"),
        remedy="repeat observations now" if recency < 0.6 else "",
    ))

    # 3. Population fit -- are we inside the calibration we actually have?
    if snapshot.age_band is AgeBand.UNKNOWN:
        fit, fit_detail, fit_remedy = 0.35, "age not recorded; no age-banded thresholds apply", "record age"
    elif snapshot.age_band is AgeBand.NEONATE:
        fit, fit_detail, fit_remedy = 0.5, "neonate: narrow ranges, high consequence, thin calibration", "senior paediatric review"
    elif snapshot.age_band.is_paediatric:
        fit, fit_detail, fit_remedy = 0.8, f"paediatric band ({snapshot.age_band.label}); PEWS-style score applied", ""
    else:
        fit, fit_detail, fit_remedy = 1.0, f"{snapshot.age_band.label}; NEWS2 applies", ""
    factors.append(ConfidenceFactor("population_fit", fit, fit_detail, fit_remedy))

    # 4. Coherence -- does the score have enough parameters to mean anything,
    #    and do the recorded vitals contradict each other?
    scored_n = len(score.components)
    coherence = min(1.0, scored_n / 5.0) if scored_n else 0.0
    contradictions: list[str] = []
    if snapshot.sbp is not None and snapshot.dbp is not None and snapshot.dbp >= snapshot.sbp:
        contradictions.append("diastolic >= systolic")
    if snapshot.spo2 is not None and snapshot.spo2 < 85 and snapshot.can_complete_sentence:
        contradictions.append("severe hypoxia with unimpaired speech")
    if contradictions:
        coherence *= 0.4
    factors.append(ConfidenceFactor(
        name="coherence",
        score=round(coherence, 3),
        detail=(f"{scored_n} scored parameters"
                + (f"; contradictory: {'; '.join(contradictions)}" if contradictions else "")),
        remedy="re-measure and confirm" if contradictions else "",
    ))

    # Weakest-link aggregation, not a mean: one badly broken factor should not
    # be averaged away by three healthy ones.
    mean = sum(f.score for f in factors) / len(factors)
    weakest = min(f.score for f in factors)
    overall = round(0.5 * mean + 0.5 * weakest, 3)

    if overall >= 0.75:
        level = ConfidenceLevel.HIGH
    elif overall >= 0.55:
        level = ConfidenceLevel.MODERATE
    elif overall >= 0.30:
        level = ConfidenceLevel.LOW
    else:
        level = ConfidenceLevel.ABSTAIN

    drivers = tuple(f"{f.name}: {f.detail}" for f in factors if f.is_weak)
    return Confidence(level=level, score=overall, factors=tuple(factors), drivers=drivers)
