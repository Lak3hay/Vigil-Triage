"""Twenty patients, chosen to exercise the things that actually go wrong.

The Round 2 brief asks for scoring on 15-20 records including "at least one
ambiguous presentation, one pediatric/geriatric case, and one zero-history
(first-time) patient". This cohort covers all three several times over, and
every entry carries ``why_included`` - the specific failure mode it tests -
so the coverage is auditable rather than assumed.

Composition follows the brief's own reference parameters: roughly half the
patients have a prior health record and half do not, matching *"roughly half of
arriving patients have some prior health record on file, half do not"*.

**These are synthetic patients, written by hand.** No real person is
represented. Their vital-sign ranges are calibrated against the open-access
MIMIC-IV-ED demo subset (222 real de-identified ED stays, ODbL) so the
distributions are plausible rather than invented - see ``reports/calibration.md``.
The engine runs unmodified on both.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from vigil.triage.snapshot import PatientSnapshot

#: Arbitrary but fixed clock, so every run of the demo is reproducible.
T0 = datetime(2026, 9, 2, 14, 0)


@dataclass(frozen=True)
class Scenario:
    """One synthetic patient, plus what they are here to test."""

    snapshot: PatientSnapshot
    why_included: str
    expected: str
    #: Later observations, as ``(minutes_after_arrival, {vital: value})``.
    #: Present only for patients whose story is a trajectory rather than a
    #: single moment.
    follow_up: tuple[tuple[int, dict], ...] = ()

    @property
    def id(self) -> str:
        return self.snapshot.patient_id


def _p(pid: str, **kw) -> PatientSnapshot:
    base = dict(patient_id=pid, observed_at=T0, consciousness="A", on_oxygen=False)
    base.update(kw)
    return PatientSnapshot(**base)


SCENARIOS: tuple[Scenario, ...] = (
    # ── the hero case: silent deterioration in the waiting room ───────────────
    Scenario(
        _p("P17", age_years=68, sex="F", chief_complaint="abdominal pain",
           arrival_mode="walk-in", pain_score=5,
           hr=88, rr=18, spo2=99, sbp=124, dbp=76, temp_c=37.1,
           has_prior_record=True, on_beta_blocker=True, nurse_acuity=3),
        why_included="The whole thesis. Triaged correctly as level 3, then deteriorates "
                     "in the waiting room while every individual reading stays inside "
                     "its normal range.",
        expected="Level 3 at arrival. At +40 min the trend fires as SILENT deterioration; "
                 "clock tightens to 15 min; she climbs the queue.",
        follow_up=((40, dict(hr=99, rr=20, spo2=95, sbp=112)),
                   (75, dict(hr=112, rr=24, spo2=93, sbp=104))),
    ),

    # ── paediatric: the case an adult-calibrated model gets wrong ─────────────
    Scenario(
        _p("P22", age_years=4, sex="F", chief_complaint="fever and not drinking",
           arrival_mode="walk-in", hr=165, rr=34, spo2=96, sbp=95, temp_c=39.1,
           has_prior_record=False, nurse_acuity=3),
        why_included="Compensated shock. Tachycardic for age with a still-normal blood "
                     "pressure - an adult model reads 'tachycardic but normotensive' and "
                     "calls it stable.",
        expected="Escalated to level 1. PEWS-style instrument, not NEWS2.",
    ),
    Scenario(
        _p("P08", age_years=0.5, sex="M", chief_complaint="fever, feeding well",
           arrival_mode="walk-in", hr=150, rr=40, spo2=98, sbp=90, temp_c=38.2,
           has_prior_record=True, nurse_acuity=4),
        why_included="The control for P22. HR 150 and RR 40 are alarming in an adult and "
                     "unremarkable in an infant. Tests that age banding discriminates "
                     "rather than simply alerting more.",
        expected="Not escalated. Age-appropriate ranges applied.",
    ),
    Scenario(
        _p("P19", age_years=15, sex="M", chief_complaint="asthma, wheeze since morning",
           arrival_mode="walk-in", hr=124, rr=28, spo2=93, sbp=118, temp_c=36.9,
           can_complete_sentence=False, work_of_breathing="moderate",
           has_prior_record=True, nurse_acuity=2),
        why_included="Speech-limiting breathlessness - observed at the desk but almost "
                     "never recorded, so it never reaches any score.",
        expected="Escalated to level 1 on the speech red flag.",
    ),

    # ── zero-history / minimal data ───────────────────────────────────────────
    Scenario(
        _p("P31", age_years=None, chief_complaint="feels unwell",
           arrival_mode="walk-in", has_prior_record=False, nurse_acuity=4),
        why_included="Zero-history first-time patient with no age and no vitals yet - "
                     "the brief's explicit case, and the one where a system must say "
                     "'I do not know' rather than guess.",
        expected="ABSTAIN. Clock capped at 30 min. Names the next most useful action.",
    ),
    Scenario(
        _p("P26", age_years=41, sex="M", chief_complaint="repeat prescription",
           arrival_mode="walk-in", hr=74, rr=14, spo2=99, sbp=126, dbp=80, temp_c=36.7,
           has_prior_record=False, nurse_acuity=5),
        why_included="A genuinely well patient with no record. Tests that the system "
                     "adds nothing when there is nothing to add.",
        expected="Level 5, no escalation, no flags, long re-check interval.",
    ),

    # ── ambiguous presentations ───────────────────────────────────────────────
    Scenario(
        _p("P04", age_years=58, sex="F", chief_complaint="indigestion and nausea, sweaty",
           arrival_mode="walk-in", pain_score=4,
           hr=96, rr=19, spo2=97, sbp=138, dbp=84, temp_c=36.9,
           has_prior_record=True, is_diabetic=True, nurse_acuity=4),
        why_included="Atypical ACS. Woman, diabetic, no chest pain - sorted as "
                     "gastrointestinal. One of the most consistently missed presentations.",
        expected="Escalated to level 2 with an ECG recommendation.",
    ),
    Scenario(
        _p("P09", age_years=72, sex="M", chief_complaint="dizziness and unsteady walking",
           arrival_mode="walk-in", hr=78, rr=16, spo2=97, sbp=152, dbp=88, temp_c=36.6,
           has_prior_record=True, vascular_risk=True, nurse_acuity=4),
        why_included="Posterior circulation stroke presenting as vertigo. Standard stroke "
                     "scales are insensitive to it and it is commonly called peripheral.",
        expected="Escalated to level 2; HINTS exam recommended.",
    ),
    Scenario(
        _p("P12", age_years=45, sex="M", chief_complaint="sudden severe back pain",
           arrival_mode="ambulance", pain_score=9,
           hr=104, rr=20, spo2=97, sbp=158, dbp=92, temp_c=36.8,
           has_prior_record=False, nurse_acuity=3),
        why_included="Aortic dissection. 'Back pain' sorts to the musculoskeletal pile and "
                     "inter-arm blood pressure is rarely measured at triage.",
        expected="Escalated to level 1; inter-arm BP and pulse deficit prompted.",
    ),
    Scenario(
        _p("P14", age_years=19, sex="F", chief_complaint="cannot breathe properly, tingling hands",
           arrival_mode="walk-in", hr=118, rr=26, spo2=98, sbp=112, dbp=70, temp_c=36.8,
           has_prior_record=False, nurse_acuity=4),
        why_included="The hardest ambiguity in the set. Reads as hyperventilation and "
                     "usually is - but tachycardia with a normal saturation is also how a "
                     "pulmonary embolism presents in a young woman.",
        expected="Escalated on physiology alone, without the system claiming a diagnosis.",
        follow_up=((55, dict(hr=132, rr=30, spo2=93, sbp=104)),),
    ),
    Scenario(
        _p("P38", age_years=38, sex="M", chief_complaint="severe pain in left leg",
           arrival_mode="walk-in", pain_score=10,
           hr=102, rr=18, spo2=98, sbp=132, dbp=82, temp_c=37.6,
           has_prior_record=False, nurse_acuity=3),
        why_included="Pain out of proportion to examination - necrotising infection, "
                     "compartment syndrome, mesenteric ischaemia. The exam looks benign so "
                     "the reported pain gets discounted.",
        expected="Escalated to level 2 with the differential named.",
    ),

    # ── geriatric ─────────────────────────────────────────────────────────────
    Scenario(
        _p("P02", age_years=81, sex="M", chief_complaint="generally weak, off legs since yesterday",
           arrival_mode="ambulance", hr=96, rr=22, spo2=95, sbp=106, dbp=64, temp_c=36.2,
           has_prior_record=True, nurse_acuity=4),
        why_included="Occult sepsis in the elderly. Afebrile, non-specific complaint - "
                     "'no fever' is read as reassurance and 'weakness' sorts low.",
        expected="Escalated to level 2; lactate and septic screen recommended.",
        follow_up=((45, dict(hr=110, rr=26, spo2=93, sbp=98)),),
    ),
    Scenario(
        _p("P29", age_years=88, sex="F", chief_complaint="fall at home, hit head",
           arrival_mode="ambulance", hr=82, rr=18, spo2=96, sbp=142, dbp=78, temp_c=36.5,
           has_prior_record=True, on_anticoagulant=True, nurse_acuity=3),
        why_included="Head injury on an anticoagulant - a minor mechanism with a major "
                     "bleeding risk. Tests that retrieved medication history changes the "
                     "reading of an otherwise unremarkable presentation.",
        expected="Anticoagulation surfaced to the clinician alongside the level.",
    ),
    Scenario(
        _p("P35", age_years=70, sex="F", chief_complaint="wound looks worse, discharged Tuesday",
           arrival_mode="walk-in", hr=98, rr=20, spo2=96, sbp=118, dbp=72, temp_c=37.9,
           has_prior_record=True, discharged_within_72h=True, ed_visits_90d=2,
           nurse_acuity=4),
        why_included="Bounce-back within 72 hours. Re-attendance is associated with missed "
                     "or evolving diagnoses, yet the prior discharge is usually read as "
                     "reassurance.",
        expected="Escalated to level 3; re-evaluate from first principles.",
        follow_up=((60, dict(hr=116, rr=24, spo2=94, temp_c=38.9)),),
    ),

    # ── immunosuppression, hypoxia, relative hypotension ──────────────────────
    Scenario(
        _p("P06", age_years=62, sex="F", chief_complaint="fever since last night",
           arrival_mode="walk-in", hr=104, rr=20, spo2=96, sbp=112, dbp=70, temp_c=38.4,
           has_prior_record=True, immunosuppressed=True, nurse_acuity=3),
        why_included="Neutropenic sepsis. The patient often looks well, and the "
                     "immunosuppression is not asked about.",
        expected="Escalated to level 1; door-to-antibiotic under one hour.",
    ),
    Scenario(
        _p("P25", age_years=25, sex="M", chief_complaint="cough and mild breathlessness",
           arrival_mode="walk-in", hr=94, rr=20, spo2=92, sbp=124, dbp=76, temp_c=37.8,
           can_complete_sentence=True, has_prior_record=False, nurse_acuity=4),
        why_included="Silent hypoxia. Comfortable and talking with a saturation of 92 - "
                     "the patient looks well, so the number gets doubted before it is believed.",
        expected="Escalated to level 2 on saturation despite a reassuring appearance.",
        follow_up=((50, dict(hr=108, rr=26, spo2=89)),),
    ),
    Scenario(
        _p("P21", age_years=50, sex="F", chief_complaint="vomiting and diarrhoea for two days",
           arrival_mode="walk-in", hr=108, rr=18, spo2=98, sbp=118, dbp=70, temp_c=37.4,
           has_prior_record=True, baseline_sbp=170, nurse_acuity=4),
        why_included="Relative hypotension. 118 systolic is population-normal and is shock "
                     "in a patient who runs 170. Delta beats absolute whenever a baseline exists.",
        expected="Escalated on the drop from her own baseline, not on the raw number.",
    ),

    # ── straightforward cases, and the anti-starvation pair ───────────────────
    Scenario(
        _p("P01", age_years=55, sex="M", chief_complaint="central chest pain, crushing",
           arrival_mode="ambulance", pain_score=8,
           hr=98, rr=20, spo2=96, sbp=142, dbp=88, temp_c=36.8,
           has_prior_record=True, nurse_acuity=2),
        why_included="A textbook presentation the nurse gets right. Tests that the system "
                     "agrees quietly instead of manufacturing a disagreement.",
        expected="Level 2 confirmed. No escalation.",
    ),
    Scenario(
        _p("P11", age_years=34, sex="F", chief_complaint="laceration to forearm",
           arrival_mode="walk-in", pain_score=3,
           hr=76, rr=14, spo2=99, sbp=118, dbp=74, temp_c=36.8,
           has_prior_record=False, nurse_acuity=4),
        why_included="Low acuity, long wait. Demonstrates anti-starvation: her cost of "
                     "waiting rises until she overtakes higher-band patients who arrived later.",
        expected="Level 4, unescalated, climbing the queue purely through waiting.",
    ),
    Scenario(
        _p("P23", age_years=29, sex="F", chief_complaint="ankle sprain playing football",
           arrival_mode="walk-in", pain_score=4,
           hr=80, rr=14, spo2=99, sbp=116, dbp=72, temp_c=36.6,
           has_prior_record=True, nurse_acuity=5),
        why_included="Genuinely minor. The system must leave it alone - a tool that "
                     "escalates everything has moved harm into over-triage, not removed it.",
        expected="Level 5. No flags, no escalation.",
    ),
)


def cohort() -> tuple[Scenario, ...]:
    return SCENARIOS


def by_id(pid: str) -> Scenario:
    for s in SCENARIOS:
        if s.id == pid:
            return s
    raise KeyError(f"no scenario {pid!r}")


def coverage() -> dict[str, list[str]]:
    """Which rubric requirement each patient covers - auditable, not asserted."""
    return {
        "ambiguous presentation": ["P04", "P09", "P12", "P14", "P38"],
        "paediatric": ["P22", "P08", "P19"],
        "geriatric": ["P02", "P17", "P29", "P35"],
        "zero-history (first-time)": ["P31", "P26", "P12", "P14", "P25", "P38", "P11", "P22"],
        "minimal data / abstention": ["P31"],
        "deterioration in the waiting room": ["P17"],
        "must NOT be escalated": ["P08", "P23", "P26", "P01"],
    }


def composition() -> dict[str, float]:
    """Cohort make-up, against the brief's reference parameters."""
    n = len(SCENARIOS)
    with_record = sum(1 for s in SCENARIOS if s.snapshot.has_prior_record)
    paeds = sum(1 for s in SCENARIOS if s.snapshot.age_band.is_paediatric)
    return {
        "patients": n,
        "with_prior_record_pct": round(100 * with_record / n, 1),
        "paediatric_pct": round(100 * paeds / n, 1),
        "arrived_by_ambulance_pct": round(
            100 * sum(1 for s in SCENARIOS if s.snapshot.arrival_mode == "ambulance") / n, 1),
    }
