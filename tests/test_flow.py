"""Queue policy, the WATCH loop, and the properties they are supposed to have."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from vigil.flow import WaitingRoom, detect_trend, profile, route
from vigil.flow.policy import RURAL_DISTRICT, URBAN_TRAUMA_CENTRE, HarmPolicy
from vigil.flow.room import EventKind
from vigil.flow.watch import Observation
from vigil.triage import PatientSnapshot

T0 = datetime(2026, 9, 2, 14, 0)


def _p(pid, **kw) -> PatientSnapshot:
    base = dict(patient_id=pid, observed_at=T0, consciousness="A",
                chief_complaint="unwell", age_years=40, hr=76, rr=14,
                spo2=98, sbp=120, dbp=76, temp_c=36.8, nurse_acuity=4)
    base.update(kw)
    return PatientSnapshot(**base)


class TestAntiStarvation:
    """The property that makes the queue policy defensible rather than merely clever."""

    def test_a_low_acuity_patient_eventually_overtakes_a_higher_one(self):
        pol = URBAN_TRAUMA_CENTRE
        l3_at_45 = pol.cost_of_waiting(3, 45)
        assert pol.cost_of_waiting(4, 60) < l3_at_45, "should not overtake early"
        assert pol.cost_of_waiting(4, 240) > l3_at_45, "must overtake eventually"

    def test_adjacent_levels_overtake_within_a_single_shift(self):
        """The clinically meaningful form of the guarantee.

        A level-4 patient overtakes a waiting level-3 in about 90 minutes, and
        a level-3 overtakes a waiting level-2 in under seven hours. Both sit
        inside one shift, which is what makes this a real fairness property
        rather than an asymptotic curiosity."""
        pol = URBAN_TRAUMA_CENTRE
        for lower in (3, 4, 5):
            reference = pol.cost_of_waiting(lower - 1, 30)
            assert pol.cost_of_waiting(lower, 12 * 60) > reference, (
                f"level {lower} does not overtake level {lower - 1} within a shift"
            )

    def test_all_waiting_levels_overtake_eventually(self):
        """The asymptotic guarantee - the maths, not the medicine.

        Convexity means no level is starved *forever*. But the crossing times
        for distant pairs are long enough to be practically irrelevant: a
        level 4 overtakes a level 2 after ~30 hours, a level 5 after ~4.5 days.
        Stated plainly so the property is not oversold - what does real work in
        a department is the adjacent-level guarantee above, which lands inside
        a single shift. This test proves only that the curve has no permanent
        floor, which is why the horizon here is deliberately absurd."""
        pol = URBAN_TRAUMA_CENTRE
        horizon = 30 * 24 * 60          # 30 days: proving a limit, not a use case
        for lower in (3, 4, 5):
            for higher in range(2, lower):
                reference = pol.cost_of_waiting(higher, 30)
                assert pol.cost_of_waiting(lower, horizon) > reference, (
                    f"level {lower} can NEVER overtake level {higher} - that is starvation"
                )

    def test_level_1_is_absolute_and_is_never_overtaken(self):
        """The deliberate exception. A level-1 patient needs an immediate
        life-saving intervention; letting a queue of minor complaints
        eventually outrank a cardiac arrest would not be fairness."""
        pol = URBAN_TRAUMA_CENTRE
        for level in (2, 3, 4, 5):
            assert pol.cost_of_waiting(level, 24 * 60) < pol.cost_of_waiting(1, 1)

    def test_convexity_at_or_below_one_is_rejected(self):
        """Anti-starvation is the mathematical property, so it is enforced."""
        with pytest.raises(ValueError, match="anti-starvation"):
            HarmPolicy(convexity=1.0)
        with pytest.raises(ValueError, match="anti-starvation"):
            HarmPolicy(convexity=0.8)

    def test_cost_is_monotonic_in_waiting_time(self):
        pol = URBAN_TRAUMA_CENTRE
        for level in (1, 2, 3, 4, 5):
            costs = [pol.cost_of_waiting(level, t) for t in range(0, 300, 15)]
            assert costs == sorted(costs), f"level {level} cost must never fall as they wait"


class TestDeteriorationRanking:
    """A patient getting worse must climb past patients merely older in the queue."""

    def test_deterioration_outranks_a_longer_wait(self):
        pol = URBAN_TRAUMA_CENTRE
        deteriorating = pol.cost_of_waiting(3, 60, deteriorating=True)
        stable_longer = pol.cost_of_waiting(3, 90)
        assert deteriorating > stable_longer

    def test_an_unreviewed_red_flag_raises_priority(self):
        pol = URBAN_TRAUMA_CENTRE
        assert pol.cost_of_waiting(3, 60, unresolved_flag=True) > pol.cost_of_waiting(3, 60)

    def test_acknowledging_a_flag_lowers_it_again(self):
        room = WaitingRoom(policy=URBAN_TRAUMA_CENTRE)
        room.admit(_p("A", age_years=70, chief_complaint="weakness and confusion",
                      hr=95, temp_c=37.0, nurse_acuity=3))
        t = T0 + timedelta(minutes=30)
        before = room.patients["A"].cost_of_waiting(room.policy, t)
        for f in room.patients["A"].assessment.red_flags:
            room.acknowledge_flag("A", f.id)
        assert room.patients["A"].cost_of_waiting(room.policy, t) < before


class TestSilentDeterioration:
    """The cold-open case: every reading normal, the trajectory is not."""

    def test_rising_hr_with_falling_spo2_is_caught_while_all_values_are_normal(self):
        obs = [
            Observation(at=T0, hr=88, rr=18, spo2=99, sbp=124, score_total=0),
            Observation(at=T0 + timedelta(minutes=40), hr=99, rr=20, spo2=95, sbp=112, score_total=2),
        ]
        # every latest value is inside its adult normal range
        trend = detect_trend(obs, in_normal_range={"hr": True, "rr": True, "spo2": True, "sbp": True})
        assert trend.worsening
        assert trend.silent, "must be marked as invisible to threshold checks"
        assert any("occult deterioration" in r for r in trend.reasons)

    def test_two_observations_are_enough(self):
        """No telemetry required - the deployability argument in one test."""
        obs = [Observation(at=T0, hr=88, spo2=99), Observation(at=T0 + timedelta(minutes=40), hr=99, spo2=95)]
        assert detect_trend(obs).worsening
        assert detect_trend(obs).n_observations == 2

    def test_measurement_noise_is_not_a_trend(self):
        """A manual pulse count varies by a few beats between nurses."""
        obs = [Observation(at=T0, hr=88, spo2=98), Observation(at=T0 + timedelta(minutes=40), hr=91, spo2=97)]
        assert not detect_trend(obs).worsening

    def test_improvement_is_not_deterioration(self):
        obs = [Observation(at=T0, hr=115, spo2=93), Observation(at=T0 + timedelta(minutes=40), hr=88, spo2=98)]
        assert not detect_trend(obs).worsening

    def test_a_single_observation_cannot_show_a_trend(self):
        assert not detect_trend([Observation(at=T0, hr=88)]).worsening


class TestWatchLoop:
    """Both triggers the brief mandates."""

    def test_the_clock_fires_when_a_recheck_is_overdue(self):
        room = WaitingRoom(policy=URBAN_TRAUMA_CENTRE)
        room.admit(_p("A", nurse_acuity=3))
        interval = room.patients["A"].assessment.monitoring.interval_minutes
        assert not [e for e in room.tick(T0 + timedelta(minutes=interval - 5))
                    if e.kind is EventKind.REASSESSMENT_DUE]
        assert [e for e in room.tick(T0 + timedelta(minutes=interval + 5))
                if e.kind is EventKind.REASSESSMENT_DUE]

    def test_worsening_vitals_fire_the_second_trigger(self):
        room = WaitingRoom(policy=URBAN_TRAUMA_CENTRE)
        room.admit(_p("A", hr=88, spo2=99, sbp=124, nurse_acuity=3))
        _, evs = room.record_observation("A", at=T0 + timedelta(minutes=40),
                                         hr=99, spo2=95, sbp=112)
        assert any(e.kind is EventKind.DETERIORATION for e in evs)

    def test_deterioration_tightens_the_clock_rather_than_relaxing_it(self):
        """Regression: the interval comes from the score, and a score can stay
        low while the trend is the entire finding. Without this the system
        detects deterioration and then says 'come back in four hours'."""
        room = WaitingRoom(policy=URBAN_TRAUMA_CENTRE)
        room.admit(_p("A", hr=88, spo2=99, sbp=124, nurse_acuity=3))
        before = room.patients["A"].assessment.monitoring.interval_minutes
        wp, _ = room.record_observation("A", at=T0 + timedelta(minutes=40),
                                        hr=99, spo2=95, sbp=112)
        assert wp.assessment.monitoring.interval_minutes <= 15
        assert wp.assessment.monitoring.interval_minutes < before

    def test_a_recheck_can_never_lower_the_level(self):
        """Escalation only, through the WATCH path as well as the engine."""
        room = WaitingRoom(policy=URBAN_TRAUMA_CENTRE)
        room.admit(_p("A", hr=135, rr=26, spo2=91, sbp=95, nurse_acuity=2))
        before = room.patients["A"].assessment.recommended_level
        wp, _ = room.record_observation("A", at=T0 + timedelta(minutes=30),
                                        hr=72, rr=14, spo2=99, sbp=125)
        assert wp.assessment.recommended_level <= before

    def test_undeliverable_schedule_escalates_as_a_staffing_signal(self):
        """Otherwise overdue tasks pile up silently and the nurse manager is
        right to reject the system as 'more alerts'."""
        room = WaitingRoom(policy=URBAN_TRAUMA_CENTRE, reassessment_capacity_per_hour=3)
        for i in range(12):
            room.admit(_p(f"P{i}", hr=125, rr=24, spo2=93, sbp=98, nurse_acuity=2))
        events = room.tick(T0 + timedelta(hours=3))
        cap = [e for e in events if e.kind is EventKind.CAPACITY]
        assert cap and "staffing" in cap[0].action


class TestOverride:
    """Rubric #5, and the adoption story: the clinician always wins."""

    def test_a_clinician_can_override_in_either_direction(self):
        room = WaitingRoom(policy=URBAN_TRAUMA_CENTRE)
        room.admit(_p("A", nurse_acuity=4))
        room.override("A", new_level=2, by="RN.Sharma", reason="looks unwell", at=T0)
        assert room.patients["A"].effective_level == 2
        room.override("A", new_level=5, by="Dr.Iyer", reason="reviewed, minor", at=T0)
        assert room.patients["A"].effective_level == 5

    def test_the_override_event_records_who_what_and_why(self):
        room = WaitingRoom(policy=URBAN_TRAUMA_CENTRE)
        room.admit(_p("A", nurse_acuity=4))
        ev = room.override("A", new_level=3, by="RN.Sharma", reason="tendon involvement", at=T0)
        assert "RN.Sharma" in ev.detail and "tendon involvement" in ev.detail
        assert "4 -> 3" in ev.detail

    def test_an_override_changes_the_queue_position(self):
        room = WaitingRoom(policy=URBAN_TRAUMA_CENTRE)
        room.admit(_p("A", nurse_acuity=4))
        room.admit(_p("B", nurse_acuity=3))
        t = T0 + timedelta(minutes=30)
        assert [p.patient_id for p, _ in room.ranked(t)] == ["B", "A"]
        room.override("A", new_level=1, by="Dr.Iyer", reason="deteriorated in waiting room", at=t)
        assert [p.patient_id for p, _ in room.ranked(t)][0] == "A"


class TestSiteProfiles:
    """The brief: a workflow for an urban trauma centre may not transfer to a
    small rural department. The engine does not change - only the policy."""

    def test_profiles_differ_in_their_declared_targets(self):
        assert RURAL_DISTRICT.target_minutes[3] > URBAN_TRAUMA_CENTRE.target_minutes[3]
        assert RURAL_DISTRICT.target_minutes[2] < URBAN_TRAUMA_CENTRE.target_minutes[2]

    def test_the_same_patient_ranks_differently_under_different_policies(self):
        urban = WaitingRoom(policy=URBAN_TRAUMA_CENTRE)
        rural = WaitingRoom(policy=RURAL_DISTRICT)
        for room in (urban, rural):
            room.admit(_p("A", nurse_acuity=3))
        t = T0 + timedelta(minutes=70)
        assert urban.patients["A"].cost_of_waiting(urban.policy, t) != \
               rural.patients["A"].cost_of_waiting(rural.policy, t)

    def test_both_profiles_keep_the_anti_starvation_guarantee(self):
        for pol in (URBAN_TRAUMA_CENTRE, RURAL_DISTRICT):
            assert pol.convexity > 1.0
            # a long-waiting level 5 overtakes a recently-arrived level 4...
            assert pol.cost_of_waiting(5, 12 * 60) > pol.cost_of_waiting(4, 30)
            # ...but never a level 1
            assert pol.cost_of_waiting(5, 12 * 60) < pol.cost_of_waiting(1, 1)

    def test_unknown_profile_names_the_alternatives(self):
        with pytest.raises(KeyError, match="urban-trauma-500"):
            profile("nonexistent")


class TestRouting:
    """'Prioritize and route' - the second verb in the problem statement."""

    def test_children_go_to_the_paediatric_area(self):
        assert route(level=3, age_band_is_paediatric=True).stream == "paediatrics"

    def test_a_critically_ill_child_goes_to_resus_not_paediatrics(self):
        assert route(level=1, age_band_is_paediatric=True).stream == "resuscitation"

    def test_an_unresolved_flag_keeps_a_level_3_out_of_ambulatory(self):
        plain = route(level=3, age_band_is_paediatric=False)
        flagged = route(level=3, age_band_is_paediatric=False, red_flag_ids=("atypical_acs",))
        assert plain.stream == "ambulatory" and flagged.stream == "majors"

    def test_isolation_overrides_everything_else(self):
        assert route(level=4, age_band_is_paediatric=False, needs_isolation=True).stream == "majors"

    def test_routing_is_always_a_recommendation(self):
        """Moving a patient to the wrong area can delay care, so its worst case
        is not wasted effort - it sits outside what the system may decide."""
        for lvl in (1, 2, 3, 4, 5):
            assert route(level=lvl, age_band_is_paediatric=False).is_recommendation
