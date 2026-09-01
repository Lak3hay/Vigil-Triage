"""What is actually known about a patient at a moment in time.

Scoped deliberately to data available in the **first few minutes of arrival**,
which was the Round 1 brief's second question and remains the binding
constraint. Anything requiring a lab, an ECG read or imaging is out: a model
that needs troponin cannot help with a decision made 45 minutes before the
troponin results.

Every field is optional. The brief assumes roughly half of arrivals have a
prior record and half do not, so a snapshot with almost nothing in it is a
normal input, not an error - it produces a *low-confidence* assessment, never
a refusal to look.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime

from vigil.clinical.agebands import AgeBand, band_for_age

#: Vitals a triage nurse can obtain at the bedside within ~2 minutes.
VITAL_FIELDS = ("hr", "rr", "spo2", "sbp", "dbp", "temp_c")


@dataclass(frozen=True)
class PatientSnapshot:
    """One patient, as known at ``observed_at``.

    Attributes
    ----------
    nurse_acuity
        The nurse's own 1-5 level, committed **before** seeing Vigil's output.
        This is the blind-second-opinion design from Round 1: it protects
        independent clinical judgement from automation bias, and it yields
        labelled disagreement data for free. It is used to enforce the
        escalation-only rule and is **never** a model input.
    has_prior_record
        Whether anything was retrievable from the health record (ABHA/ABDM in
        our assumed jurisdiction). False is the "zero-history first-time
        patient" the brief requires us to handle.
    """

    patient_id: str
    observed_at: datetime

    # demographics
    age_years: float | None = None
    sex: str | None = None

    # presentation
    chief_complaint: str = ""
    arrival_mode: str | None = None          # walk-in / ambulance / wheelchair / carried
    pain_score: float | None = None
    can_complete_sentence: bool | None = None
    work_of_breathing: str | None = None     # normal / mild / moderate / severe

    # vitals
    hr: float | None = None
    rr: float | None = None
    spo2: float | None = None
    sbp: float | None = None
    dbp: float | None = None
    temp_c: float | None = None
    consciousness: str | None = None         # ACVPU
    on_oxygen: bool | None = None
    glucose_mmol: float | None = None

    # retrieved history
    has_prior_record: bool = False
    on_beta_blocker: bool = False
    immunosuppressed: bool = False
    on_anticoagulant: bool = False
    is_diabetic: bool = False
    is_pregnant: bool | None = None
    vascular_risk: bool = False
    discharged_within_72h: bool = False
    ed_visits_90d: int = 0
    baseline_sbp: float | None = None

    # the human's own call, committed first
    nurse_acuity: int | None = None

    # bookkeeping
    arrived_at: datetime | None = None
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    # ── derived ───────────────────────────────────────────────────────────────
    @property
    def age_band(self) -> AgeBand:
        return band_for_age(self.age_years)

    @property
    def present_vitals(self) -> tuple[str, ...]:
        return tuple(f for f in VITAL_FIELDS if getattr(self, f) is not None)

    @property
    def missing_vitals(self) -> tuple[str, ...]:
        return tuple(f for f in VITAL_FIELDS if getattr(self, f) is None)

    @property
    def shock_index(self) -> float | None:
        """HR / systolic BP. Above ~0.9 suggests occult hypoperfusion.

        Useful precisely because it fires while both components still read
        individually normal - the same trends-not-thresholds argument in a
        single number.
        """
        if self.hr is None or self.sbp is None or self.sbp <= 0:
            return None
        return round(self.hr / self.sbp, 3)

    @property
    def sbp_drop_from_baseline(self) -> float | None:
        """Points below this patient's own usual systolic pressure.

        110/70 is normal unless the patient runs 170/95, in which case it is
        shock. Delta beats absolute whenever a baseline exists.
        """
        if self.sbp is None or self.baseline_sbp is None:
            return None
        return round(self.baseline_sbp - self.sbp, 1)

    @property
    def complaint_terms(self) -> frozenset[str]:
        """Lower-cased tokens of the chief complaint, for rule matching.

        Deliberately crude. Free-text understanding is the one place an LLM
        earns its keep, and it is designed-not-built here (README); the rules
        must work without it.
        """
        cleaned = "".join(c.lower() if c.isalnum() else " " for c in self.chief_complaint)
        return frozenset(cleaned.split())

    def mentions(self, *terms: str) -> bool:
        """True if any term appears in the chief complaint."""
        t = self.complaint_terms
        return any(term.lower() in t for term in terms)

    def with_vitals(self, **kw) -> "PatientSnapshot":
        """A copy with updated vitals - used by the WATCH loop on re-check."""
        return replace(self, **kw)
