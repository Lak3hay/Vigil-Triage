"""The encoded red-flag panel - the lethal presentations a model cannot learn.

The load-bearing argument of this whole project:

    The failures that kill are rare **by construction**. You cannot learn what
    you rarely see. So they are encoded, not learned.

A model trained on emergency-department data will underperform on exactly the
presentations that kill, because they are underrepresented in the training set
*by definition*. No amount of data fixes this; it is a property of the problem.
Each rule below is a documented, auditable, individually-testable statement of
a known miss - the kind of institutional knowledge that normally lives only in
a senior nurse's head.

Every rule carries a ``rationale`` shown verbatim to the clinician. A rule that
fires without saying why is an alarm, not decision support, and the brief
requires recommendations to be explainable in seconds.

**These are illustrative encodings of published clinical patterns, tuned for a
prototype.** A real deployment would have them reviewed, versioned and signed
off by the department's clinical governance lead - which is exactly why they
live in one readable file rather than inside model weights.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from vigil.clinical.agebands import AgeBand, hypotension_sbp_for

if TYPE_CHECKING:  # import for typing only -- vigil.triage imports this module
    from vigil.triage.snapshot import PatientSnapshot


@dataclass(frozen=True)
class RedFlag:
    """One encoded miss.

    Attributes
    ----------
    escalate_to
        Minimum acuity this presentation demands (1 = most urgent). The engine
        takes the most urgent level across all fired flags; a flag can only
        ever *raise* urgency.
    why_missed
        Why this presentation gets under-triaged in practice. Displayed to the
        clinician, because the point is not to overrule them but to surface the
        thing that is easy to miss at 3 a.m. in a full department.
    """

    id: str
    name: str
    escalate_to: int
    rationale: str
    why_missed: str
    condition: Callable[[PatientSnapshot], bool]

    def fires(self, s: PatientSnapshot) -> bool:
        try:
            return bool(self.condition(s))
        except Exception:  # a broken rule must not take the engine down
            return False


def _is_elderly(s: PatientSnapshot) -> bool:
    return s.age_band is AgeBand.GERIATRIC


# ── the panel ─────────────────────────────────────────────────────────────────

PANEL: tuple[RedFlag, ...] = (
    RedFlag(
        id="occult_sepsis_elderly",
        name="Possible occult sepsis (elderly, no fever)",
        escalate_to=2,
        rationale=(
            "Elderly patient with non-specific weakness/confusion and no fever. "
            "Absence of fever does not exclude sepsis in this group - many are "
            "afebrile or hypothermic. Consider lactate and a septic screen."
        ),
        why_missed="'No fever' is read as reassurance, and 'weakness' sorts to a low acuity.",
        condition=lambda s: (
            _is_elderly(s)
            and (s.mentions("weakness", "weak", "confusion", "confused", "unwell",
                            "lethargy", "lethargic", "malaise", "fall", "falls")
                 or (s.consciousness or "A").upper() != "A")
            and (s.temp_c is None or s.temp_c < 38.0)
            and ((s.hr is not None and s.hr > 90)
                 or (s.rr is not None and s.rr > 20)
                 or (s.sbp is not None and s.sbp < 110)
                 or (s.temp_c is not None and s.temp_c < 36.0))
        ),
    ),
    RedFlag(
        id="atypical_acs",
        name="Possible atypical acute coronary syndrome",
        escalate_to=2,
        rationale=(
            "Cardiac-risk patient with an atypical presentation - jaw, epigastric, "
            "back or shoulder pain, nausea, or unexplained breathlessness. Women, "
            "diabetics and the elderly frequently present without chest pain. "
            "Recommend immediate ECG."
        ),
        why_missed="No chest pain, so it is sorted as gastrointestinal or musculoskeletal.",
        condition=lambda s: (
            (s.is_diabetic or (s.sex or "").upper().startswith("F") or _is_elderly(s))
            and s.mentions("jaw", "epigastric", "indigestion", "heartburn", "nausea",
                           "vomiting", "shoulder", "arm", "sweating", "diaphoretic",
                           "breathless", "dyspnoea", "dyspnea", "fatigue")
            and not s.mentions("trauma", "injury", "fracture", "laceration")
        ),
    ),
    RedFlag(
        id="posterior_stroke",
        name="Possible posterior circulation stroke",
        escalate_to=2,
        rationale=(
            "Vertigo, unsteadiness or visual disturbance in a patient with vascular "
            "risk factors. Posterior strokes are commonly missed as peripheral "
            "vertigo and are poorly detected by standard stroke scales. "
            "Recommend HINTS exam and urgent imaging pathway."
        ),
        why_missed="Looks like benign vertigo; NIHSS is insensitive to posterior territory.",
        condition=lambda s: (
            (s.vascular_risk or s.is_diabetic or _is_elderly(s))
            and s.mentions("vertigo", "dizzy", "dizziness", "unsteady", "imbalance",
                           "double", "diplopia", "slurred", "ataxia", "nystagmus")
        ),
    ),
    RedFlag(
        id="aortic_dissection",
        name="Possible aortic dissection",
        escalate_to=1,
        rationale=(
            "Severe or tearing chest/back pain of abrupt onset. Check for an "
            "inter-arm blood-pressure differential and pulse deficit before "
            "assuming a musculoskeletal cause."
        ),
        why_missed="'Back pain' sorts into the musculoskeletal pile; inter-arm BP is rarely measured at triage.",
        condition=lambda s: (
            s.mentions("tearing", "ripping", "dissection")
            or (s.mentions("back", "chest") and s.mentions("sudden", "severe", "worst", "abrupt"))
        ),
    ),
    RedFlag(
        id="compensated_shock_paediatric",
        name="Possible compensated shock (child)",
        escalate_to=1,
        rationale=(
            "Child with tachycardia for age and a still-normal blood pressure. "
            "Children maintain pressure by vasoconstriction until they "
            "decompensate suddenly - a normal BP here is not reassurance. "
            "Check capillary refill, lactate and perfusion now."
        ),
        why_missed="Normal blood pressure is read as stability. In children BP falls last.",
        condition=lambda s: (
            s.age_band.is_paediatric
            and s.hr is not None
            and s.hr > _paed_hr_ceiling(s)
            and (s.sbp is None or s.sbp >= hypotension_sbp_for(s.age_band, s.age_years))
        ),
    ),
    RedFlag(
        id="beta_blocker_masking",
        name="Beta-blocker may be masking tachycardia",
        escalate_to=3,
        rationale=(
            "Patient takes a beta-blocker, which blunts the heart-rate response to "
            "shock, sepsis and haemorrhage. Interpret any rise in pulse as more "
            "significant than its absolute value suggests, and weight respiratory "
            "rate and perfusion more heavily."
        ),
        why_missed="A 'normal' pulse is taken at face value when the drug is suppressing it.",
        condition=lambda s: (
            s.on_beta_blocker
            and ((s.hr is not None and s.hr > 80)
                 or (s.rr is not None and s.rr > 20)
                 or (s.sbp is not None and s.sbp < 110)
                 or (s.temp_c is not None and (s.temp_c >= 38.0 or s.temp_c < 36.0)))
        ),
    ),
    RedFlag(
        id="neutropenic_sepsis",
        name="Immunosuppressed with fever - treat as neutropenic sepsis",
        escalate_to=1,
        rationale=(
            "Fever in an immunosuppressed patient (chemotherapy, transplant, "
            "dialysis, long-term steroids) is an emergency however well they look. "
            "Target door-to-antibiotic under one hour."
        ),
        why_missed="The patient often looks well, and the immunosuppression is not asked about.",
        condition=lambda s: (
            s.immunosuppressed
            and ((s.temp_c is not None and s.temp_c >= 38.0)
                 or s.mentions("fever", "febrile", "temperature", "chills", "rigors"))
        ),
    ),
    RedFlag(
        id="bounce_back",
        name="Return visit within 72 hours",
        escalate_to=3,
        rationale=(
            "Discharged from this department within the last 72 hours. Re-attendance "
            "is associated with missed or evolving diagnoses - re-evaluate from "
            "first principles rather than anchoring on the previous assessment."
        ),
        why_missed="The prior discharge is read as reassurance rather than as a warning.",
        condition=lambda s: s.discharged_within_72h,
    ),
    RedFlag(
        id="pain_out_of_proportion",
        name="Pain out of proportion to examination",
        escalate_to=2,
        rationale=(
            "Severe pain with unremarkable findings. Consider necrotising soft-tissue "
            "infection, compartment syndrome, or mesenteric ischaemia - all "
            "time-critical and all easy to under-call early."
        ),
        why_missed="Examination looks benign, so the reported pain is discounted.",
        condition=lambda s: (
            s.pain_score is not None and s.pain_score >= 8
            and s.mentions("pain")
            and not s.mentions("fracture", "laceration", "burn", "trauma", "injury")
        ),
    ),
    RedFlag(
        id="silent_hypoxia",
        name="Hypoxia with preserved speech",
        escalate_to=2,
        rationale=(
            "Oxygen saturation is low while the patient is still talking comfortably. "
            "Preserved conversation is not evidence of adequate oxygenation; the "
            "compensation is what hides it."
        ),
        why_missed="The patient looks and sounds well, so the saturation is doubted before it is believed.",
        condition=lambda s: s.spo2 is not None and s.spo2 < 94,
    ),
    RedFlag(
        id="cannot_complete_sentence",
        name="Unable to complete a sentence",
        escalate_to=1,
        rationale=(
            "Breathlessness severe enough to interrupt speech indicates significant "
            "respiratory distress regardless of the recorded saturation."
        ),
        why_missed="Speech effort is observed but rarely recorded, so it never reaches the score.",
        condition=lambda s: s.can_complete_sentence is False,
    ),
    RedFlag(
        id="relative_hypotension",
        name="Blood pressure low for this patient",
        escalate_to=2,
        rationale=(
            "Systolic pressure is well below this patient's own recorded baseline. "
            "A population-normal reading can still be shock in a patient who "
            "usually runs hypertensive."
        ),
        why_missed="The absolute number sits inside the normal range, so nothing flags.",
        condition=lambda s: (
            s.sbp_drop_from_baseline is not None and s.sbp_drop_from_baseline >= 40
        ),
    ),
)


def _paed_hr_ceiling(s: PatientSnapshot) -> float:
    from vigil.clinical.agebands import ranges_for
    return float(ranges_for(s.age_band).hr[1])


def evaluate(snapshot: PatientSnapshot) -> tuple[RedFlag, ...]:
    """Every rule that fires for this patient, most urgent first."""
    fired = [f for f in PANEL if f.fires(snapshot)]
    return tuple(sorted(fired, key=lambda f: f.escalate_to))


def escalation_floor(fired: tuple[RedFlag, ...]) -> int | None:
    """Most urgent level demanded by any fired rule, or ``None`` if none fired."""
    return min((f.escalate_to for f in fired), default=None)
