"""Integration boundary, retention, consent, and the two-second explanation."""
from __future__ import annotations

import pytest

from vigil.audit.log import CONSENT_BASIS, RETENTION
from vigil.integrations import (
    DEFAULT_BED_SOURCE,
    DEFAULT_RECORD_SOURCE,
    DEFAULT_ROSTER_SOURCE,
    TIERS,
    BedManagementSource,
    IntegrationUnavailable,
    PatientRecordSource,
    StaffRosterSource,
    current_tier,
)
from vigil.sim import SCENARIOS, by_id
from vigil.triage import assess
from vigil.triage.confidence import ConfidenceLevel


class TestIntegrationBoundary:
    """Not implemented - but the interface is published, which is the point."""

    @pytest.mark.parametrize("source", [DEFAULT_RECORD_SOURCE, DEFAULT_BED_SOURCE,
                                        DEFAULT_ROSTER_SOURCE])
    def test_an_absent_integration_refuses_loudly(self, source):
        """Never a silent None. An integration that fails quietly is
        indistinguishable from a patient with genuinely no history."""
        with pytest.raises(IntegrationUnavailable):
            getattr(source, "lookup", source.area_state)("x") if hasattr(source, "lookup") \
                else source.area_state()

    def test_the_refusal_names_what_would_be_needed(self):
        with pytest.raises(IntegrationUnavailable, match="medications"):
            DEFAULT_RECORD_SOURCE.lookup("abha-123")
        with pytest.raises(IntegrationUnavailable, match="occupied"):
            DEFAULT_BED_SOURCE.area_state()
        with pytest.raises(IntegrationUnavailable, match="on shift"):
            DEFAULT_ROSTER_SOURCE.on_shift()

    def test_it_says_vigil_keeps_working_without_it(self):
        with pytest.raises(IntegrationUnavailable, match="never blocks"):
            DEFAULT_RECORD_SOURCE.lookup("x")

    def test_the_protocols_are_narrow_by_design(self):
        """Data minimisation is a legal obligation, and a narrow request is
        also the one an integration team can actually satisfy."""
        for proto in (PatientRecordSource, BedManagementSource, StaffRosterSource):
            methods = [m for m in dir(proto) if not m.startswith("_")]
            assert len(methods) <= 2, f"{proto.__name__} asks for too much"


class TestCapabilityTiers:
    def test_nothing_integrated_is_tier_zero_and_still_runs(self):
        assert current_tier().level == "T0"
        a = assess(by_id("P22").snapshot)
        assert a.recommended_level == 1, "the clinical engine must work at T0"

    def test_tiers_escalate_with_what_is_available(self):
        rec, beds, roster = object(), object(), object()
        assert current_tier(record=rec).level == "T1"
        assert current_tier(record=rec, beds=beds).level == "T2"
        assert current_tier(record=rec, beds=beds, roster=roster).level == "T3"

    def test_an_unimplemented_adapter_does_not_count_as_present(self):
        assert current_tier(record=DEFAULT_RECORD_SOURCE).level == "T0"

    def test_most_of_the_value_lands_at_tier_one(self):
        """A system whose benefit requires T3 is one almost nobody can deploy."""
        assert "medications" in TIERS[1].unlocks or "masking" in TIERS[1].unlocks


class TestGovernance:
    """The brief: the jurisdiction affects retention policy and consent model."""

    def test_retention_distinguishes_clinical_from_operational(self):
        assert "clinical_decision_record" in RETENTION
        assert "operational_telemetry" in RETENTION
        assert RETENTION["clinical_decision_record"] != RETENTION["operational_telemetry"]

    def test_the_clinical_record_is_not_given_a_short_clock(self):
        """It is evidence in exactly the disputes an audit trail exists for."""
        assert "episode of care" in RETENTION["clinical_decision_record"]

    def test_the_disagreement_record_is_de_linked(self):
        assert "never linked to an individual" in RETENTION["disagreement_record"]

    def test_model_improvement_is_not_covered_by_the_treatment_basis(self):
        """The one genuinely new processing purpose, named as such."""
        assert "not covered by the treatment basis" in CONSENT_BASIS["model_improvement"]

    def test_the_record_is_contestable(self):
        assert "contestable" in CONSENT_BASIS["patient_rights"]


class TestTwoSecondExplanation:
    """'explainable within seconds, by a clinician managing several patients'."""

    def test_every_patient_has_one(self):
        for s in SCENARIOS:
            line = assess(s.snapshot).one_line
            assert line and len(line) < 110, f"{s.id}: too long to glance at - {line}"

    def test_it_names_the_action_not_just_the_level(self):
        a = assess(by_id("P22").snapshot)
        assert a.one_line.startswith("Escalate")
        assert "3 -> 1" in a.one_line

    def test_it_leads_with_the_reason_when_there_is_one(self):
        a = assess(by_id("P04").snapshot)
        assert "coronary" in a.one_line.lower()

    def test_abstention_says_what_would_help(self):
        a = assess(by_id("P31").snapshot)
        assert a.confidence.level is ConfidenceLevel.ABSTAIN
        assert "Not enough" in a.one_line
        assert "record" in a.one_line

    def test_a_confirmed_level_does_not_pretend_to_be_news(self):
        a = assess(by_id("P26").snapshot)
        assert "confirmed" in a.one_line and "Escalate" not in a.one_line

    def test_it_travels_in_the_audit_record(self):
        assert assess(by_id("P22").snapshot).to_dict()["one_line"]
