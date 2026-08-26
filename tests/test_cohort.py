"""Cohort construction and exclusion-accounting tests."""
import pandas as pd
import pytest

from vigil.data.cohort import (
    CENSORING_DISPOSITIONS,
    CohortError,
    CohortSpec,
    build_cohort,
    characterise,
    missingness_by_channel,
)
from vigil.data.exclusions import ExclusionReport

T0 = pd.Timestamp("2150-01-01 10:00")


def _stays(specs):
    """specs: list of (stay_id, patient_id, hours, disposition)."""
    return pd.DataFrame([
        {"stay_id": s, "patient_id": p, "intime": T0,
         "outtime": T0 + pd.Timedelta(hours=h), "disposition": d}
        for s, p, h, d in specs
    ])


def _vitals(stay_ids):
    return pd.DataFrame({
        "stay_id": list(stay_ids), "patient_id": [1] * len(stay_ids),
        "charttime": [T0] * len(stay_ids),
        "hr": [80.0] * len(stay_ids), "rr": [16.0] * len(stay_ids),
        "spo2": [98.0] * len(stay_ids), "sbp": [120.0] * len(stay_ids),
        "dbp": [70.0] * len(stay_ids), "temp_c": [37.0] * len(stay_ids),
    })


class TestRefusalOnUnsatisfiableConstraint:
    """A constraint that cannot be satisfied must raise, never silently vanish.
    MIMIC-IV-ED's edstays has no age column; asking for adults must not quietly
    return children too."""

    def test_age_bound_without_age_column_raises(self):
        stays = _stays([(1, 1, 5, "HOME")])
        with pytest.raises(CohortError, match="no `age` column"):
            build_cohort(stays, _vitals([1]), CohortSpec(min_age=18))

    def test_the_error_names_where_age_actually_lives(self):
        stays = _stays([(1, 1, 5, "HOME")])
        with pytest.raises(CohortError, match="anchor_age"):
            build_cohort(stays, _vitals([1]), CohortSpec(max_age=89))

    def test_age_filter_works_when_the_column_is_present(self):
        stays = _stays([(1, 1, 5, "HOME"), (2, 2, 5, "HOME")])
        stays["age"] = [12, 40]
        cohort, rep = build_cohort(stays, _vitals([1, 2]), CohortSpec(min_age=18))
        assert cohort["stay_id"].tolist() == [2]
        assert rep.reasons["age below 18"] == 1


class TestCensoringIsNotExclusion:
    """Leaving without being seen is not a negative outcome -- it is the end of
    observation. Dropping it would delete a group enriched for long waits."""

    @pytest.mark.parametrize("disp", CENSORING_DISPOSITIONS)
    def test_censored_stays_are_retained(self, disp):
        stays = _stays([(1, 1, 5, disp)])
        cohort, _ = build_cohort(stays, _vitals([1]))
        assert len(cohort) == 1

    @pytest.mark.parametrize("disp", CENSORING_DISPOSITIONS)
    def test_censored_stays_are_flagged(self, disp):
        cohort, _ = build_cohort(_stays([(1, 1, 5, disp)]), _vitals([1]))
        assert bool(cohort["is_censored"].iloc[0])

    def test_ordinary_dispositions_are_not_flagged(self):
        cohort, _ = build_cohort(_stays([(1, 1, 5, "ADMITTED")]), _vitals([1]))
        assert not bool(cohort["is_censored"].iloc[0])


class TestDurationFilters:
    """Hand-computable: stays of 0.1h, 5h and 100h against a 30min/72h window."""

    SPEC = CohortSpec(min_stay_minutes=30, max_stay_hours=72.0)

    def test_short_stay_dropped_as_landmarkless(self):
        stays = _stays([(1, 1, 0.1, "HOME"), (2, 2, 5, "HOME")])
        cohort, rep = build_cohort(stays, _vitals([1, 2]), self.SPEC)
        assert cohort["stay_id"].tolist() == [2]
        assert rep.reasons["stay shorter than 30 min (no landmark possible)"] == 1

    def test_implausibly_long_stay_dropped(self):
        stays = _stays([(1, 1, 100, "HOME"), (2, 2, 5, "HOME")])
        cohort, rep = build_cohort(stays, _vitals([1, 2]), self.SPEC)
        assert cohort["stay_id"].tolist() == [2]
        assert "administrative artifact" in "".join(rep.reasons)

    def test_non_positive_duration_dropped(self):
        stays = _stays([(1, 1, 0, "HOME"), (2, 2, 5, "HOME")])
        cohort, rep = build_cohort(stays, _vitals([1, 2]), self.SPEC)
        assert cohort["stay_id"].tolist() == [2]
        assert rep.reasons["non-positive stay duration"] == 1

    def test_max_stay_can_be_disabled(self):
        stays = _stays([(1, 1, 100, "HOME")])
        cohort, _ = build_cohort(stays, _vitals([1]), CohortSpec(max_stay_hours=None))
        assert len(cohort) == 1

    def test_stay_minutes_is_derived(self):
        cohort, _ = build_cohort(_stays([(1, 1, 2, "HOME")]), _vitals([1]))
        assert cohort["stay_minutes"].iloc[0] == 120.0


class TestObservationRequirement:
    def test_stays_without_vitals_dropped_and_counted(self):
        stays = _stays([(1, 1, 5, "HOME"), (2, 2, 5, "HOME")])
        cohort, rep = build_cohort(stays, _vitals([1]))  # stay 2 has none
        assert cohort["stay_id"].tolist() == [1]
        assert rep.reasons["no observations recorded"] == 1

    def test_can_be_disabled(self):
        stays = _stays([(1, 1, 5, "HOME"), (2, 2, 5, "HOME")])
        cohort, _ = build_cohort(stays, _vitals([1]),
                                 CohortSpec(require_any_observation=False))
        assert len(cohort) == 2


class TestStratifiedExclusionAudit:
    """PLAN.md 3.2 -- the machinery that makes a biasing filter visible.
    Hand-computable: a filter removing 1/10 survivors and 5/10 deaths has a
    disparity of 50 - 10 = 40 percentage points."""

    def _planted(self):
        rep = ExclusionReport(stage="t", unit="stay", n_input=20, n_output=14)
        rep.drop([1], "harmless filter")                 # 1 of 10 survivors
        rep.drop([11, 12, 13, 14, 15], "biasing filter")  # 5 of 10 deaths
        outcome = pd.Series(
            {**{i: "survived" for i in range(1, 11)}, **{i: "died" for i in range(11, 21)}}
        )
        return rep, outcome

    def test_disparity_is_computed_correctly(self):
        rep, outcome = self._planted()
        s = rep.stratify(outcome).set_index("reason")
        assert s.loc["biasing filter", "disparity"] == 50.0
        assert s.loc["harmless filter", "disparity"] == 10.0

    def test_per_class_rates_are_reported(self):
        rep, outcome = self._planted()
        s = rep.stratify(outcome).set_index("reason")
        assert s.loc["biasing filter", "pct[died]"] == 50.0
        assert s.loc["biasing filter", "pct[survived]"] == 0.0

    def test_worst_offender_is_surfaced_first(self):
        rep, outcome = self._planted()
        assert rep.stratify(outcome).iloc[0]["reason"] == "biasing filter"
        assert rep.worst_disparity(outcome) == ("biasing filter", 50.0)

    def test_audit_is_possible_after_the_fact(self):
        """Labels arrive later than the cohort, so identities must be kept."""
        stays = _stays([(1, 1, 0.1, "HOME"), (2, 2, 5, "HOME"), (3, 3, 5, "HOME")])
        _, rep = build_cohort(stays, _vitals([1, 2, 3]))
        outcome = pd.Series({1: "died", 2: "survived", 3: "survived"})
        assert rep.stratify(outcome).iloc[0]["disparity"] > 0

    def test_no_exclusions_yields_an_empty_audit(self):
        rep = ExclusionReport(stage="t", n_input=3, n_output=3)
        assert rep.stratify(pd.Series({1: "a"})).empty
        assert rep.worst_disparity(pd.Series({1: "a"})) is None


class TestCharacterise:
    def test_reports_stays_and_patients_separately(self):
        stays = _stays([(1, 1, 5, "HOME"), (2, 1, 5, "HOME"), (3, 2, 5, "ADMITTED")])
        cohort, _ = build_cohort(stays, _vitals([1, 2, 3]))
        t1 = characterise(cohort)["all"]
        assert t1["stays"] == 3 and t1["patients"] == 2
        assert t1["stays per patient"] == 1.5

    def test_length_of_stay_reported_as_median_not_mean(self):
        """LOS is right-skewed; a mean describes a patient who does not exist."""
        stays = _stays([(i, i, h, "HOME") for i, h in enumerate([2, 2, 2, 2, 60], 1)])
        cohort, _ = build_cohort(stays, _vitals(range(1, 6)), CohortSpec(max_stay_hours=None))
        assert characterise(cohort)["all"]["stay hours, median [IQR]"].startswith("2.0")

    def test_can_stratify(self):
        stays = _stays([(1, 1, 5, "HOME"), (2, 2, 5, "ADMITTED")])
        cohort, _ = build_cohort(stays, _vitals([1, 2]))
        out = characterise(cohort, by="disposition")
        assert {"all", "HOME", "ADMITTED"} <= set(out.columns)


class TestMissingnessReport:
    def test_distinguishes_observation_level_from_stay_level(self):
        """A channel measured once then never again is present at the stay level
        and absent for most of the trajectory. Different questions."""
        v = _vitals([1, 1, 1])
        v.loc[1:, "temp_c"] = None  # measured once out of three
        cohort, _ = build_cohort(_stays([(1, 1, 5, "HOME")]), v)
        m = missingness_by_channel(cohort, v).set_index("channel")
        assert m.loc["temp_c", "pct_observations_missing"] == pytest.approx(66.7, abs=0.1)
        assert m.loc["temp_c", "pct_stays_never_recorded"] == 0.0

    def test_channel_never_recorded_shows_at_stay_level(self):
        v = _vitals([1, 1])
        v["temp_c"] = None
        cohort, _ = build_cohort(_stays([(1, 1, 5, "HOME")]), v)
        m = missingness_by_channel(cohort, v).set_index("channel")
        assert m.loc["temp_c", "pct_stays_never_recorded"] == 100.0


class TestSpecValidation:
    def test_negative_min_stay_rejected(self):
        with pytest.raises(CohortError):
            CohortSpec(min_stay_minutes=-1)

    def test_inverted_age_range_rejected(self):
        with pytest.raises(CohortError, match="min_age must not exceed"):
            CohortSpec(min_age=80, max_age=18)

    def test_non_positive_max_stay_rejected(self):
        with pytest.raises(CohortError):
            CohortSpec(max_stay_hours=0)


class TestUnitMismatchGuard:
    """A stage can remove units finer than the outcome is indexed at -- landmark
    drops keyed by stay_id, where one stay contributes many landmarks. Dividing
    landmark counts by stay counts yields a number that looks like a percentage
    and is not."""

    def _landmark_style(self):
        rep = ExclusionReport(stage="landmarks", unit="landmark", n_input=20, n_output=14)
        rep.drop([1, 1, 1, 2, 2, 3], "dropped several landmarks per stay")
        outcome = pd.Series({1: "admitted", 2: "admitted", 3: "home"})
        return rep, outcome

    def test_repeated_ids_without_a_denominator_raise(self):
        from vigil.data.exclusions import UnitMismatchError

        rep, outcome = self._landmark_style()
        with pytest.raises(UnitMismatchError, match="mixes units"):
            rep.stratify(outcome)

    def test_explicit_denominator_makes_it_well_defined(self):
        """2 admitted stays x 4 landmark slots = 8; 5 dropped -> 62.5%."""
        rep, outcome = self._landmark_style()
        out = rep.stratify(outcome, denominator=pd.Series({"admitted": 8, "home": 4}))
        assert out.iloc[0]["pct[admitted]"] == 62.5
        assert out.iloc[0]["pct[home]"] == 25.0

    def test_unique_ids_still_need_no_denominator(self):
        rep = ExclusionReport(stage="cohort", unit="stay", n_input=3, n_output=2)
        rep.drop([1], "short stay")
        out = rep.stratify(pd.Series({1: "died", 2: "survived", 3: "survived"}))
        assert out.iloc[0]["pct[died]"] == 100.0
