"""The audit chain must actually detect tampering, not merely claim to."""
from __future__ import annotations

import itertools
from datetime import datetime

import pytest

from vigil.audit import AuditLog, TamperError, record_assessment, record_override
from vigil.audit.log import GENESIS, AuditEntry
from vigil.triage import PatientSnapshot, assess

T0 = datetime(2026, 9, 2, 14, 0)


def _log_with(n: int = 5) -> AuditLog:
    log = AuditLog()
    for i in range(n):
        log.append(actor="vigil", action="assessment", subject=f"P{i}",
                   payload={"recommended_level": 3}, at=T0)
    return log


class TestChainIntegrity:
    def test_a_clean_chain_verifies(self):
        ok, msg = _log_with().verify()
        assert ok and "intact" in msg

    def test_an_empty_log_verifies(self):
        assert AuditLog().verify()[0]

    def test_the_first_entry_links_to_genesis(self):
        assert _log_with(1).entries[0].prev_hash == GENESIS

    def test_each_entry_links_to_the_one_before(self):
        log = _log_with()
        for prev, nxt in itertools.pairwise(log.entries):
            assert nxt.prev_hash == prev.entry_hash


class TestTamperDetection:
    """A guard that has never been seen to fire is not protecting anything."""

    def test_editing_a_payload_is_caught_and_located(self):
        log = _log_with()
        log.entries[2] = AuditEntry(**{**log.entries[2].to_dict(),
                                       "payload": {"recommended_level": 5}})
        ok, msg = log.verify()
        assert not ok and "index 2" in msg

    def test_editing_the_actor_is_caught(self):
        log = _log_with()
        log.entries[1] = AuditEntry(**{**log.entries[1].to_dict(), "actor": "someone-else"})
        assert not log.verify()[0]

    def test_deleting_an_entry_breaks_the_link(self):
        log = _log_with()
        del log.entries[2]
        ok, msg = log.verify()
        assert not ok and "broken link" in msg

    def test_reordering_entries_is_caught(self):
        log = _log_with()
        log.entries[1], log.entries[3] = log.entries[3], log.entries[1]
        assert not log.verify()[0]

    def test_assert_intact_raises_with_the_location(self):
        log = _log_with()
        log.entries[0] = AuditEntry(**{**log.entries[0].to_dict(), "subject": "X"})
        with pytest.raises(TamperError, match="index 0"):
            log.assert_intact()

    def test_recomputing_the_whole_chain_would_pass(self):
        """Honesty check on the claim: this is tamper-EVIDENT, not tamper-proof.

        An attacker who can rewrite the entire file can rebuild every hash.
        The README says so; this test makes sure the claim in the README is the
        claim the code actually supports."""
        log = _log_with(3)
        rebuilt = AuditLog()
        for e in log.entries:
            rebuilt.append(actor=e.actor, action=e.action, subject=e.subject,
                           payload={"recommended_level": 1}, at=T0)
        assert rebuilt.verify()[0], "a full rewrite verifies - hence 'evident', not 'proof'"


class TestAppendOnly:
    def test_there_is_no_update_or_delete_in_the_interface(self):
        api = {m for m in dir(AuditLog) if not m.startswith("_")}
        assert not (api & {"update", "delete", "remove", "edit", "pop"})

    def test_indices_are_dense_and_ordered(self):
        log = _log_with(6)
        assert [e.index for e in log.entries] == list(range(6))


class TestRoundTrip:
    def test_a_written_log_reads_back_and_still_verifies(self, tmp_path):
        log = _log_with(4)
        path = log.write(tmp_path / "audit.jsonl")
        reloaded = AuditLog.read(path)
        assert len(reloaded) == 4
        assert reloaded.verify()[0]
        assert reloaded.head == log.head

    def test_tampering_with_the_file_is_caught_on_reload(self, tmp_path):
        log = _log_with(4)
        path = log.write(tmp_path / "audit.jsonl")
        lines = path.read_text(encoding="utf-8").splitlines()
        lines[1] = lines[1].replace('"recommended_level":3', '"recommended_level":5')
        path.write_text("\n".join(lines), encoding="utf-8")
        assert not AuditLog.read(path).verify()[0]


class TestClinicalRecording:
    def _snap(self, **kw) -> PatientSnapshot:
        base = dict(patient_id="P1", observed_at=T0, age_years=68, chief_complaint="chest pain",
                    hr=104, rr=22, spo2=94, sbp=104, temp_c=38.2, consciousness="A",
                    nurse_acuity=3)
        base.update(kw)
        return PatientSnapshot(**base)

    def test_an_assessment_is_recorded_reproducibly(self):
        """A recommendation nobody can reproduce is not reviewable."""
        log = AuditLog()
        a = assess(self._snap())
        e = record_assessment(log, a)
        assert e.actor == a.engine_version
        assert e.payload["recommended_level"] == a.recommended_level
        assert e.payload["score_components"] == dict(a.score.components)
        assert e.payload["rationale"], "the reasoning must be stored, not just the number"

    def test_missing_parameters_are_recorded_not_silently_zeroed(self):
        log = AuditLog()
        a = assess(self._snap(rr=None, spo2=None))
        e = record_assessment(log, a)
        assert set(e.payload["score_missing"]) >= {"respiratory_rate", "spo2"}

    def test_an_override_records_both_sides_of_the_disagreement(self):
        """Reviewing an override without the reasoning it overruled tells you
        nothing about whether either was right."""
        log = AuditLog()
        a = assess(self._snap())
        record_assessment(log, a)
        e = record_override(log, patient_id="P1", clinician="Dr.Iyer", was=a.recommended_level,
                            now_level=4, reason="known chronic, seen yesterday",
                            recommendation_rationale=a.rationale, at=T0)
        assert e.payload["direction"] == "de-escalation"
        assert e.payload["reason"]
        assert e.payload["superseded_rationale"], "must keep what it overruled"
        assert log.verify()[0]

    def test_overrides_are_queryable_for_the_disagreement_analysis(self):
        log = AuditLog()
        a = assess(self._snap())
        record_assessment(log, a)
        record_override(log, patient_id="P1", clinician="Dr.Iyer", was=3, now_level=2,
                        reason="unwell on review", at=T0)
        assert len(log.overrides()) == 1
        assert len(log.for_subject("P1")) == 2


class TestDataMinimisation:
    """DPDP obligation, not a nicety: log the reasoning, not the person."""

    def test_no_direct_identifiers_reach_the_log(self):
        log = AuditLog()
        a = assess(PatientSnapshot(patient_id="P1", observed_at=T0, age_years=68,
                                   chief_complaint="chest pain", hr=104, nurse_acuity=3))
        payload = record_assessment(log, a).payload
        forbidden = {"name", "phone", "address", "email", "abha_id", "mrn", "dob"}
        assert not (set(payload) & forbidden)
