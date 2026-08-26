"""Split tests.

The property that matters is negative -- *no patient appears twice* -- so the
overlap guard is tested by planting a violation, not only by passing clean input.
"""
import pandas as pd
import pytest

from vigil.data.splits import (
    PatientOverlapError,
    SplitError,
    SplitSpec,
    apply_splits,
    assert_no_patient_overlap,
    make_splits,
    split_summary,
)


def _stays(n_patients=20, stays_each=3):
    rows = []
    for p in range(n_patients):
        for s in range(stays_each):
            rows.append({"stay_id": p * 100 + s, "patient_id": p,
                         "intime": pd.Timestamp("2150-01-01"),
                         "outtime": pd.Timestamp("2150-01-02"), "disposition": "HOME"})
    return pd.DataFrame(rows)


class TestOverlapGuard:
    def test_clean_splits_pass(self):
        assert_no_patient_overlap(pd.DataFrame({"patient_id": [1, 2, 3],
                                                "split": ["train", "val", "test"]}))

    def test_guard_fires_on_a_planted_violation(self):
        """Falsification: if this cannot fail, it is not protecting anything."""
        bad = pd.DataFrame({"patient_id": [1, 1], "split": ["train", "test"]})
        with pytest.raises(PatientOverlapError, match="more than one split"):
            assert_no_patient_overlap(bad)

    def test_every_generated_split_is_checked(self):
        s = make_splits(_stays(), SplitSpec(strategy="patient_random"))
        assert_no_patient_overlap(s)
        assert s["patient_id"].is_unique


class TestPatientGrouping:
    """The reason this module exists: 3.5 stays per patient in the real data."""

    def test_all_stays_of_a_patient_land_in_one_split(self):
        stays = _stays(n_patients=30, stays_each=4)
        s = make_splits(stays, SplitSpec(strategy="patient_random"))
        joined = apply_splits(stays, s)
        per_patient = joined.groupby("patient_id")["split"].nunique()
        assert (per_patient == 1).all(), "a patient's stays were split across the wall"

    def test_splits_are_exhaustive_and_disjoint(self):
        stays = _stays()
        s = make_splits(stays, SplitSpec(strategy="patient_random"))
        assert set(s["split"]) <= {"train", "val", "test"}
        assert len(s) == stays["patient_id"].nunique()


class TestTemporalStrategy:
    """MIMIC shifts dates per patient, so `intime` cannot order patients."""

    def test_refuses_without_an_anchor_and_says_why(self):
        with pytest.raises(SplitError, match="anchor_year_group"):
            make_splits(_stays(), SplitSpec(strategy="patient_temporal"))

    def test_error_names_the_interim_workaround(self):
        with pytest.raises(SplitError, match="patient_random"):
            make_splits(_stays(), SplitSpec(strategy="patient_temporal"))

    def test_orders_by_anchor_so_test_is_the_latest_patients(self):
        stays = _stays(n_patients=10, stays_each=1)
        anchor = pd.Series({p: 2000 + p for p in range(10)})
        s = make_splits(stays, SplitSpec("patient_temporal", (0.6, 0.2, 0.2)), anchor=anchor)
        by = s.set_index("patient_id")["split"]
        assert by.loc[0] == "train" and by.loc[9] == "test"
        assert max(p for p in by.index if by[p] == "train") < \
               min(p for p in by.index if by[p] == "test")

    def test_missing_anchor_entries_are_rejected(self):
        stays = _stays(n_patients=10, stays_each=1)
        with pytest.raises(SplitError, match="anchor missing"):
            make_splits(stays, SplitSpec("patient_temporal"),
                        anchor=pd.Series({p: 2000 for p in range(5)}))

    def test_ties_broken_reproducibly(self):
        stays = _stays(n_patients=12, stays_each=1)
        anchor = pd.Series({p: 2010 for p in range(12)})  # all identical
        a = make_splits(stays, SplitSpec("patient_temporal"), anchor=anchor)
        b = make_splits(stays, SplitSpec("patient_temporal"), anchor=anchor)
        pd.testing.assert_frame_equal(a, b)


class TestRandomStrategy:
    def test_same_seed_same_split(self):
        stays = _stays()
        a = make_splits(stays, SplitSpec("patient_random", seed=7))
        b = make_splits(stays, SplitSpec("patient_random", seed=7))
        pd.testing.assert_frame_equal(a, b)

    def test_different_seed_different_split(self):
        stays = _stays(n_patients=50, stays_each=1)
        a = make_splits(stays, SplitSpec("patient_random", seed=1))
        b = make_splits(stays, SplitSpec("patient_random", seed=2))
        assert not a["split"].equals(b["split"])

    def test_fractions_are_respected(self):
        stays = _stays(n_patients=100, stays_each=1)
        s = make_splits(stays, SplitSpec("patient_random", (0.7, 0.15, 0.15)))
        counts = s["split"].value_counts()
        assert counts["train"] == 70 and counts["val"] == 15 and counts["test"] == 15


class TestApplyAndSummarise:
    def test_unknown_patient_is_an_error_not_a_null(self):
        stays = _stays(n_patients=10, stays_each=1)
        s = make_splits(stays, SplitSpec("patient_random"))
        extra = pd.concat([stays, pd.DataFrame([{"stay_id": 999, "patient_id": 999,
                                                 "intime": pd.Timestamp("2150-01-01"),
                                                 "outtime": pd.Timestamp("2150-01-02"),
                                                 "disposition": "HOME"}])], ignore_index=True)
        with pytest.raises(SplitError, match="absent from the split table"):
            apply_splits(extra, s)

    def test_frame_without_patient_id_is_rejected(self):
        s = make_splits(_stays(), SplitSpec("patient_random"))
        with pytest.raises(SplitError, match="no patient_id"):
            apply_splits(pd.DataFrame({"stay_id": [1]}), s)

    def test_summary_counts_patients_not_rows(self):
        stays = _stays(n_patients=20, stays_each=3)
        joined = apply_splits(stays, make_splits(stays, SplitSpec("patient_random")))
        summ = split_summary(joined)
        assert summ["patients"].sum() == 20
        assert summ["rows"].sum() == 60


class TestSpecValidation:
    @pytest.mark.parametrize("fr", [(0.5, 0.2, 0.2), (0.5, 0.6, 0.1), (-0.1, 0.5, 0.6)])
    def test_bad_fractions_rejected(self, fr):
        with pytest.raises(SplitError):
            SplitSpec(fractions=fr)

    def test_unknown_strategy_rejected(self):
        with pytest.raises(SplitError, match="unknown strategy"):
            SplitSpec(strategy="stratified")

    def test_too_few_patients_rejected(self):
        with pytest.raises(SplitError, match="at least 3 patients"):
            make_splits(_stays(n_patients=2, stays_each=1), SplitSpec("patient_random"))
