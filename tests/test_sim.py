"""The synthetic cohort, the surge, and the counterfactual."""
from __future__ import annotations

import pytest

from vigil.sim import (SCENARIOS, by_id, compare, composition, coverage,
                       generate_surge, run_shift, surge_summary)
from vigil.triage import assess
from vigil.triage.confidence import ConfidenceLevel


class TestCohortMeetsTheRubric:
    """The brief's Minimum Prototype Expectations, checked rather than claimed."""

    def test_at_least_fifteen_records(self):
        assert len(SCENARIOS) >= 15

    def test_every_patient_scores_with_a_confidence_indicator(self):
        for s in SCENARIOS:
            a = assess(s.snapshot)
            assert a.confidence.level in set(ConfidenceLevel)
            assert a.confidence.factors

    @pytest.mark.parametrize("requirement", [
        "ambiguous presentation", "paediatric", "geriatric", "zero-history (first-time)",
    ])
    def test_each_required_case_type_is_present(self, requirement):
        ids = coverage()[requirement]
        assert ids
        for pid in ids:
            by_id(pid)  # raises if the id does not exist

    def test_every_patient_documents_why_it_is_here(self):
        """Coverage is auditable rather than asserted."""
        for s in SCENARIOS:
            assert len(s.why_included) > 40
            assert s.expected

    def test_composition_matches_the_briefs_reference_parameters(self):
        """'roughly half of arriving patients have some prior health record'."""
        c = composition()
        assert 40 <= c["with_prior_record_pct"] <= 70
        assert c["paediatric_pct"] > 0


class TestCohortBehaviour:
    """The cases that must escalate, and - just as important - those that must not."""

    def test_the_paediatric_shock_case_escalates_to_level_one(self):
        a = assess(by_id("P22").snapshot)
        assert a.recommended_level == 1 and a.escalated
        assert "PEWS" in a.score.instrument

    def test_the_infant_control_is_not_escalated(self):
        """Same vitals shape, different age. If this escalates, the age band is
        just a louder alarm rather than a discriminator."""
        a = assess(by_id("P08").snapshot)
        assert not a.escalated

    def test_the_zero_history_patient_abstains(self):
        a = assess(by_id("P31").snapshot)
        assert a.confidence.level is ConfidenceLevel.ABSTAIN
        assert a.requires_human_review
        assert a.confidence.next_best_action

    def test_the_well_patients_are_left_alone(self):
        for pid in coverage()["must NOT be escalated"]:
            assert not assess(by_id(pid).snapshot).escalated, f"{pid} should not escalate"

    def test_the_hero_case_starts_unremarkable(self):
        """P17 must look fine at arrival, or the demo proves nothing."""
        a = assess(by_id("P17").snapshot)
        assert a.score.total <= 2
        assert a.recommended_level == 3

    def test_every_escalation_names_a_reason(self):
        for s in SCENARIOS:
            a = assess(s.snapshot)
            if a.escalated:
                assert a.red_flags or a.computed_level is not None
                assert a.rationale


class TestSurge:
    def test_three_times_volume_is_generated(self):
        summary = surge_summary(generate_surge(hours=3, multiplier=3.0))
        assert summary["arrivals_per_hour"] > 2.5 * 21

    def test_surge_is_reproducible(self):
        a = generate_surge(hours=2, seed=7)
        b = generate_surge(hours=2, seed=7)
        assert [s.snapshot.hr for _, s in a] == [s.snapshot.hr for _, s in b]

    def test_crowding_degrades_triage_accuracy(self):
        """Without this the surge test only proves the queue got longer, and
        misses the failure the queue exists to catch."""
        summary = surge_summary(generate_surge(hours=3, multiplier=3.0))
        assert summary["triage_degraded_by_crowding"] > 0

    def test_degradation_can_be_switched_off_for_a_control(self):
        arr = generate_surge(hours=3, degrade_triage_under_load=False)
        assert surge_summary(arr)["triage_degraded_by_crowding"] == 0

    def test_the_capacity_warning_fires_under_surge(self):
        res = run_shift(generate_surge(hours=3, multiplier=3.0),
                        ordering="vigil", minutes_per_patient=4.0, shift_minutes=180)
        assert any(e.kind.value == "capacity_warning" for e in res.events), (
            "an undeliverable reassessment schedule must escalate as a staffing signal"
        )


class TestCounterfactual:
    """The experiment. Identical arrivals, identical deteriorations, one change."""

    @staticmethod
    @pytest.fixture(scope="module")
    def result():
        return compare()

    def test_both_arms_see_the_same_number_of_patients(self, result):
        """Capacity is held constant - the claim is about ordering, not throughput.
        If these diverge, the comparison is measuring the wrong thing."""
        assert result["fifo"]["seen"] == result["vigil"]["seen"]

    def test_deteriorating_patients_are_reached_sooner(self, result):
        p = result["paired_on_deteriorating_patients"]
        assert p["n_matched"] >= 10, "too few matched patients to claim anything"
        assert p["median_change_min"] < 0
        assert p["reached_sooner"] > p["reached_later"]

    def test_the_overall_queue_is_not_made_worse(self, result):
        """Re-ordering must not be throughput improvement in disguise."""
        assert abs(result["delta"]["median_wait_min"]) < 15

    def test_the_comparison_is_paired_not_arm_level(self, result):
        """The two arms do not deteriorate the same patients, so arm-level
        medians compare different denominators."""
        p = result["paired_on_deteriorating_patients"]
        assert "n_deteriorated_vigil_only" in p and "n_deteriorated_fifo_only" in p

    def test_patients_reached_later_are_reported(self, result):
        """Re-ordering is zero-sum under fixed capacity: if somebody moves up,
        somebody moves back. Reporting only the winners would be dishonest."""
        assert "reached_later" in result["paired_on_deteriorating_patients"]

    def test_assumptions_travel_with_the_result(self, result):
        a = result["assumptions"]
        assert a["service_capacity_minutes_per_patient"] and a["shift_minutes"]
        assert "not clinical evidence" in a["note"].lower()

    def test_the_result_is_reproducible(self):
        assert compare()["paired_on_deteriorating_patients"]["median_change_min"] == \
               compare()["paired_on_deteriorating_patients"]["median_change_min"]
