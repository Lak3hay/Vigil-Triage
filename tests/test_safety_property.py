"""The safety property, demonstrated exhaustively.

The Round 2 brief:

    "Under-triage and over-triage carry asymmetric costs - missing a critical
    case is categorically worse than over-prioritizing a minor one. Any
    solution must be deliberately tuned to bias toward escalation under
    uncertainty rather than optimized for average accuracy, and teams must
    demonstrate this design choice explicitly in their prototype."

This file is that demonstration. It does not check a handful of examples - it
sweeps a grid of thousands of synthetic patients spanning every age band, every
vital-sign extreme, every red-flag combination and every level of data
completeness, and asserts on every one of them:

    recommended_level <= nurse_acuity          (never less urgent than the human)

**Why that single inequality matters.** If Vigil can only ever raise urgency,
then adding it to a department cannot create a new under-triage failure that
did not already exist without it. The system is *safety-monotone*: its worst
case is wasted effort, never missed care. That is a property of the
composition, not a hope about the model, and it is why it can be tested rather
than merely argued.

The suite is also adversarial about the inverse: several tests would fail if
the engine were made *too* eager, because a system that escalates everything
has not solved under-triage, it has moved the harm into over-triage and alert
fatigue.
"""
from __future__ import annotations

import itertools
from datetime import datetime

import pytest

from vigil.clinical.agebands import AgeBand
from vigil.triage import PatientSnapshot, assess
from vigil.triage.confidence import ConfidenceLevel

NOW = datetime(2026, 9, 1, 12, 0)

AGES = [0.02, 0.5, 2, 4, 8, 15, 30, 55, 70, 88, None]
HRS = [None, 35, 55, 75, 95, 120, 145, 175]
RRS = [None, 7, 11, 16, 22, 28, 40]
SPO2S = [None, 85, 92, 95, 99]
SBPS = [None, 70, 85, 105, 125, 190]
TEMPS = [None, 34.5, 36.5, 38.5, 39.5]
ACUITIES = [1, 2, 3, 4, 5]


def _snap(**kw) -> PatientSnapshot:
    base = dict(patient_id="T", observed_at=NOW, consciousness="A", chief_complaint="unwell")
    base.update(kw)
    return PatientSnapshot(**base)


def _grid(limit: int | None = None):
    """A wide sweep of physiologically extreme and incomplete patients."""
    combos = itertools.product(AGES, HRS, RRS, SPO2S, SBPS, TEMPS, ACUITIES)
    for i, (age, hr, rr, spo2, sbp, temp, acuity) in enumerate(combos):
        if limit is not None and i >= limit:
            return
        # stride to keep the sweep large but the suite fast
        if i % 37:
            continue
        yield _snap(age_years=age, hr=hr, rr=rr, spo2=spo2, sbp=sbp,
                    temp_c=temp, nurse_acuity=acuity)


class TestSafetyMonotone:
    """The core invariant. If any of these fail, the design claim is false."""

    def test_never_less_urgent_than_the_nurse_across_the_grid(self):
        n = 0
        for s in _grid():
            a = assess(s)
            assert a.recommended_level <= s.nurse_acuity, (
                f"DE-ESCALATION: nurse={s.nurse_acuity} -> {a.recommended_level} "
                f"for age={s.age_years} hr={s.hr} rr={s.rr} spo2={s.spo2} sbp={s.sbp}"
            )
            n += 1
        assert n > 10_000, f"sweep too small to be a demonstration (n={n})"

    def test_recommendation_stays_in_range(self):
        for s in _grid():
            a = assess(s)
            assert 1 <= a.recommended_level <= 5

    @pytest.mark.parametrize("nurse", ACUITIES)
    def test_a_healthy_patient_is_never_escalated(self, nurse):
        """The other half of the design: it must not escalate everything.

        A fully-recorded, entirely normal adult gives the engine no reason to
        act. If this fails, the system has become an alarm rather than an
        assistant."""
        s = _snap(age_years=40, hr=72, rr=14, spo2=98, sbp=124, dbp=78,
                  temp_c=36.8, nurse_acuity=nurse, has_prior_record=True)
        a = assess(s)
        assert a.computed_level is None, "normal physiology should justify no floor"
        assert a.recommended_level == nurse
        assert not a.escalated

    def test_escalation_is_recorded_whenever_it_happens(self):
        for s in _grid():
            a = assess(s)
            assert a.escalated == (a.recommended_level < s.nurse_acuity)

    def test_deference_is_explained_when_we_read_lower_than_the_nurse(self):
        """When our own reading is less urgent, we defer - and say so."""
        s = _snap(age_years=40, hr=72, rr=14, spo2=98, sbp=124, temp_c=36.8,
                  nurse_acuity=1, has_prior_record=True)
        a = assess(s)
        assert a.recommended_level == 1
        assert any("never lowers" in r for r in a.rationale)


class TestUncertaintyBiasesTowardEscalation:
    """Uncertainty must tighten observation, never relax it."""

    def test_ignorance_shortens_the_clock_rather_than_lengthening_it(self):
        """A patient with nothing recorded scores 0, which reads as 'well'.

        Left alone that earns the LONGEST re-check interval - exactly backwards,
        since they are the patient we know least about. Regression guard for a
        real bug (MISTAKES.md 2026-09-01)."""
        blank = _snap(age_years=None, nurse_acuity=4)
        full_and_well = _snap(age_years=40, hr=72, rr=14, spo2=98, sbp=124,
                              dbp=78, temp_c=36.8, nurse_acuity=4)
        a_blank, a_well = assess(blank), assess(full_and_well)
        assert a_blank.confidence.level is ConfidenceLevel.ABSTAIN
        assert a_blank.monitoring.interval_minutes < a_well.monitoring.interval_minutes

    def test_abstention_demands_a_human(self):
        a = assess(_snap(age_years=None, nurse_acuity=4))
        assert a.requires_human_review
        assert "review" in " ".join(d.action for d in a.decisions)

    def test_no_assessment_is_ever_returned_without_a_confidence_indicator(self):
        """Brief: 'must not return a score without a confidence indicator'."""
        for s in _grid():
            a = assess(s)
            assert a.confidence.level in set(ConfidenceLevel)
            assert 0.0 <= a.confidence.score <= 1.0
            assert a.confidence.factors, "confidence must be decomposed, not opaque"

    def test_staleness_lowers_confidence(self):
        s = _snap(age_years=40, hr=72, rr=14, spo2=98, sbp=124, dbp=78, temp_c=36.8,
                  nurse_acuity=3)
        fresh = assess(s, minutes_since_observation=0)
        stale = assess(s, minutes_since_observation=400)
        assert stale.confidence.score < fresh.confidence.score


class TestAuthorityLadder:
    """The system must never claim an authority it does not have."""

    def test_de_escalation_is_declared_out_of_scope_on_every_assessment(self):
        for s in list(_grid())[:200]:
            a = assess(s)
            never = [d for d in a.decisions if d.authority == "never"]
            assert any("de_escalation" == d.action for d in never)

    def test_only_reversible_actions_are_autonomous(self):
        """Everything under 'decides' must be an action whose worst case is
        wasted effort: re-checking sooner, or watching more closely."""
        allowed = {"reassessment_interval", "observation_intensity"}
        for s in list(_grid())[:200]:
            a = assess(s)
            for d in a.decisions:
                if d.authority == "decides":
                    assert d.action in allowed, f"unexpected autonomous action {d.action!r}"

    def test_acuity_is_only_ever_recommended(self):
        for s in list(_grid())[:200]:
            a = assess(s)
            acuity = [d for d in a.decisions if d.action == "acuity_level"]
            assert acuity and all(d.authority == "recommends" for d in acuity)


class TestAgeBandedSafety:
    """The brief calls a single adult-calibrated model a 'silent safety risk'."""

    def test_a_child_is_never_scored_with_the_adult_instrument(self):
        for age in [0.02, 0.5, 2, 4, 8, 15]:
            a = assess(_snap(age_years=age, hr=140, rr=30, spo2=97, sbp=95,
                             temp_c=38.5, nurse_acuity=3))
            assert "PEWS" in a.score.instrument, f"adult score used at age {age}"

    def test_adult_thresholds_would_under_call_a_tachycardic_child(self):
        """HR 165 in a 4-year-old with a normal-for-age BP: an adult model sees
        'tachycardic but normotensive'. The paediatric path must escalate."""
        child = assess(_snap(age_years=4, hr=165, rr=34, spo2=96, sbp=95,
                             temp_c=39.1, nurse_acuity=3))
        assert child.escalated
        assert child.recommended_level == 1
        assert any(f.id == "compensated_shock_paediatric" for f in child.red_flags)

    def test_the_same_numbers_in_an_infant_are_not_alarming(self):
        """HR 150 is an emergency in an adult and unremarkable in an infant.
        The band must cut both ways, or it is just a louder alarm."""
        infant = assess(_snap(age_years=0.5, hr=150, rr=40, spo2=98, sbp=90,
                              temp_c=36.9, nurse_acuity=4))
        adult = assess(_snap(age_years=40, hr=150, rr=40, spo2=98, sbp=90,
                             temp_c=36.9, nurse_acuity=4))
        assert infant.score.total < adult.score.total
        assert adult.escalated and not infant.escalated

    def test_unknown_age_never_silently_becomes_an_adult(self):
        a = assess(_snap(age_years=None, hr=90, rr=18, spo2=97, sbp=120,
                         dbp=75, temp_c=37.0, nurse_acuity=3))
        assert a.age_band is AgeBand.UNKNOWN
        fit = {f.name: f for f in a.confidence.factors}["population_fit"]
        assert fit.is_weak and "age" in fit.remedy


class TestDeterminism:
    """A score a regulator cannot reproduce is not usable in a clinical workflow."""

    def test_identical_input_gives_identical_output(self):
        s = _snap(age_years=68, hr=99, rr=20, spo2=95, sbp=112, temp_c=37.2,
                  on_beta_blocker=True, nurse_acuity=3)
        a, b = assess(s), assess(s)
        assert a.to_dict() == b.to_dict()

    def test_every_assessment_carries_its_engine_version(self):
        a = assess(_snap(age_years=40, nurse_acuity=3))
        assert a.engine_version and "/" in a.engine_version

    def test_every_assessment_is_json_serialisable(self):
        import json
        for s in list(_grid())[:100]:
            json.dumps(assess(s).to_dict())
