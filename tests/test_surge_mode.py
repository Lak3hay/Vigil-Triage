"""Surge mode must change behaviour - and must not change the wrong things."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from vigil.flow import WaitingRoom
from vigil.flow.mode import (
    DEFAULT_SURGE_RULES,
    OperatingMode,
    SurgeRules,
    adjust_recheck_interval,
    adjust_target,
    interrupts,
    recheck_demand_per_hour,
    should_enter_surge,
)
from vigil.flow.policy import URBAN_TRAUMA_CENTRE
from vigil.flow.room import EventKind
from vigil.sim import generate_surge
from vigil.triage import PatientSnapshot

T0 = datetime(2026, 9, 2, 14, 0)
RULES = DEFAULT_SURGE_RULES


def _p(pid, **kw) -> PatientSnapshot:
    base = {"patient_id": pid, "observed_at": T0, "consciousness": "A",
            "chief_complaint": "unwell", "age_years": 40, "hr": 76, "rr": 14,
            "spo2": 98, "sbp": 120, "dbp": 76, "temp_c": 36.8, "nurse_acuity": 4}
    base.update(kw)
    return PatientSnapshot(**base)


def _busy_room(n=70, capacity=8) -> WaitingRoom:
    room = WaitingRoom(policy=URBAN_TRAUMA_CENTRE, reassessment_capacity_per_hour=capacity)
    for m, s in generate_surge(hours=2.5, multiplier=3.0, seed=11)[:n]:
        room.admit(s.snapshot, now=T0 + timedelta(minutes=m))
    return room


class TestTrigger:
    """Surge is not 'many patients'. It is a schedule that cannot be delivered."""

    def test_demand_is_measured_in_the_same_unit_as_staffing(self):
        # four patients on 60-minute clocks demand four re-checks an hour
        assert recheck_demand_per_hour([60, 60, 60, 60]) == 4.0
        assert recheck_demand_per_hour([15]) == 4.0

    def test_a_long_queue_being_kept_up_with_is_not_a_surge(self):
        assert not should_enter_surge(waiting=60, demand_per_hour=5.0,
                                      capacity_per_hour=12, mode=OperatingMode.NORMAL,
                                      rules=RULES)

    def test_an_undeliverable_schedule_is(self):
        assert should_enter_surge(waiting=30, demand_per_hour=40.0,
                                  capacity_per_hour=8, mode=OperatingMode.NORMAL,
                                  rules=RULES)

    def test_a_small_queue_never_triggers_however_unstable(self):
        """Below the queue floor the department can still cope by hand."""
        assert not should_enter_surge(waiting=4, demand_per_hour=99.0,
                                      capacity_per_hour=1, mode=OperatingMode.NORMAL,
                                      rules=RULES)

    def test_hysteresis_stops_the_mode_oscillating(self):
        """A system that flips its own behaviour every few minutes is worse
        than one that never changes it."""
        stay = should_enter_surge(waiting=22, demand_per_hour=1.0, capacity_per_hour=99,
                                  mode=OperatingMode.SURGE, rules=RULES)
        enter = should_enter_surge(waiting=22, demand_per_hour=1.0, capacity_per_hour=99,
                                   mode=OperatingMode.NORMAL, rules=RULES)
        assert stay and not enter, "exit threshold must sit below the entry threshold"

    def test_a_ratio_at_or_below_one_is_rejected_as_a_config(self):
        with pytest.raises(ValueError, match="still deliverable"):
            SurgeRules(enter_demand_ratio=1.0)

    def test_missing_hysteresis_is_rejected_as_a_config(self):
        with pytest.raises(ValueError, match="hysteresis"):
            SurgeRules(enter_waiting=20, leave_waiting=20)


class TestWhatSurgeMustNeverDo:
    """The invariants. If any of these break, surge has become unsafe."""

    def test_a_deteriorating_patients_clock_never_stretches(self):
        for level in (1, 2, 3, 4, 5):
            base = 240
            out, _ = adjust_recheck_interval(base, level=level, deteriorating=True,
                                             mode=OperatingMode.SURGE, rules=RULES)
            assert out <= base, f"level {level} deteriorating clock stretched under surge"

    def test_level_one_and_two_clocks_never_stretch(self):
        for level in (1, 2):
            out, _ = adjust_recheck_interval(60, level=level, deteriorating=False,
                                             mode=OperatingMode.SURGE, rules=RULES)
            assert out <= 60

    def test_clinical_targets_do_not_move_for_the_sick(self):
        """Levels 1-3 are clinical commitments. Only service commitments stretch."""
        for level in (1, 2, 3):
            assert adjust_target(60.0, level=level, mode=OperatingMode.SURGE,
                                 rules=RULES) == 60.0

    def test_surge_cannot_cause_a_de_escalation(self):
        """Surge reallocates attention. It has no authority over acuity."""
        room = _busy_room()
        before = {p.patient_id: p.effective_level for p in room.waiting()}
        room.tick(T0 + timedelta(minutes=30))
        assert room.in_surge
        after = {p.patient_id: p.effective_level for p in room.waiting()}
        for pid, lvl in before.items():
            assert after[pid] <= lvl, f"{pid} was de-escalated by entering surge"

    def test_nothing_is_discarded_only_re_routed(self):
        """Suppressing information and batching it are different things."""
        room = _busy_room()
        room.tick(T0 + timedelta(minutes=30))
        room.tick(T0 + timedelta(minutes=90))
        assert room.batched, "sub-threshold events must be retained for the charge nurse"
        for e in room.batched:
            assert e in room.events, "a batched event must still be in the record"


class TestWhatSurgeDoesChange:
    def test_low_risk_clocks_stretch(self):
        out, why = adjust_recheck_interval(120, level=4, deteriorating=False,
                                           mode=OperatingMode.SURGE, rules=RULES)
        assert out > 120 and "deliverable" in why

    def test_low_acuity_targets_stretch(self):
        assert adjust_target(120.0, level=4, mode=OperatingMode.SURGE, rules=RULES) > 120.0

    def test_the_alert_floor_rises(self):
        assert interrupts("attention", OperatingMode.NORMAL, RULES)
        assert not interrupts("attention", OperatingMode.SURGE, RULES)
        assert interrupts("urgent", OperatingMode.SURGE, RULES), "urgent must always interrupt"

    def test_nothing_changes_in_normal_mode(self):
        """The control. If these differ, 'normal' is not a baseline."""
        assert adjust_recheck_interval(240, level=5, deteriorating=False,
                                       mode=OperatingMode.NORMAL, rules=RULES) == (240, "")
        assert adjust_target(120.0, level=5, mode=OperatingMode.NORMAL, rules=RULES) == 120.0


class TestSurgeMakesTheScheduleDeliverable:
    """The point of the whole module, measured rather than asserted."""

    def test_entering_surge_reduces_the_demanded_recheck_rate(self):
        room = _busy_room()
        waiting = room.waiting()
        normal = recheck_demand_per_hour(
            [p.recheck_interval(OperatingMode.NORMAL, RULES)[0] for p in waiting])
        surge = recheck_demand_per_hour(
            [p.recheck_interval(OperatingMode.SURGE, RULES)[0] for p in waiting])
        assert surge < normal, "surge must reduce the schedule it demands"

    def test_high_risk_patients_keep_their_cadence_exactly(self):
        """Not tightened, not stretched. Surge cannot create clinician-minutes,
        so promising the sickest a faster cadence would be a schedule nobody
        could staff - and it raised total demand when we tried it."""
        room = _busy_room()
        high = [p for p in room.waiting() if p.effective_level <= 2]
        assert high, "fixture should contain some high-acuity patients"
        for p in high:
            n, _ = p.recheck_interval(OperatingMode.NORMAL, RULES)
            s, _ = p.recheck_interval(OperatingMode.SURGE, RULES)
            assert s == n, f"{p.patient_id} at level {p.effective_level} had its clock moved"

    def test_interrupting_alerts_fall_while_the_record_is_kept(self):
        room = _busy_room()
        room.tick(T0 + timedelta(minutes=30))
        room.tick(T0 + timedelta(minutes=120))
        interrupting = room.interrupting_events()
        assert len(interrupting) < len(room.events)
        assert all(e.severity == "urgent" for e in interrupting)


class TestModeIsAudited:
    def test_entering_surge_emits_an_event_that_explains_itself(self):
        room = _busy_room()
        events = room.tick(T0 + timedelta(minutes=30))
        changes = [e for e in events if e.kind is EventKind.MODE_CHANGE]
        assert len(changes) == 1
        d = changes[0].detail
        assert "SURGE" in d and "capacity" in d and "/hour" in d
        assert changes[0].severity == "urgent"
        assert "charge nurse" in changes[0].action

    def test_the_mode_does_not_flap(self):
        """One change, not one per tick."""
        room = _busy_room()
        for t in (30, 60, 90, 120):
            room.tick(T0 + timedelta(minutes=t))
        changes = [e for e in room.events if e.kind is EventKind.MODE_CHANGE]
        assert len(changes) == 1

    def test_a_quiet_room_never_enters_surge(self):
        room = WaitingRoom(policy=URBAN_TRAUMA_CENTRE, reassessment_capacity_per_hour=12)
        for i in range(6):
            room.admit(_p(f"Q{i}"), now=T0)
        room.tick(T0 + timedelta(minutes=60))
        assert room.mode is OperatingMode.NORMAL
        assert not [e for e in room.events if e.kind is EventKind.MODE_CHANGE]
