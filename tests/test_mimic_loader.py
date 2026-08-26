"""Loader tests against the real demo subset.

Skipped when the data is absent so a clean checkout still goes green;
run `python -m vigil.data.fetch_demo` to enable them.
"""
from pathlib import Path

import pandas as pd
import pytest

from vigil.data import get_dataset
from vigil.data.schema import STAYS, TRIAGE, VITALS

ROOT = Path("data/raw/mimic-iv-ed-demo")
pytestmark = pytest.mark.skipif(not (ROOT / "ed").is_dir(), reason="demo subset not downloaded")


@pytest.fixture(scope="module")
def ds():
    return get_dataset("mimic-iv-ed", root=ROOT)


def test_stays_match_canonical_schema(ds):
    df = ds.stays()
    assert list(df.columns) == STAYS
    assert len(df) > 0


def test_triage_one_row_per_stay(ds):
    assert list(ds.triage().columns) == TRIAGE


def test_vitals_are_sorted_within_every_stay(ds):
    """The raw table is unordered; the loader's job is to fix that."""
    df = ds.vitals()
    assert list(df.columns) == VITALS
    ordered = df.groupby("stay_id")["charttime"].apply(lambda s: s.is_monotonic_increasing)
    assert ordered.all()


def test_temperature_converted_to_celsius(ds):
    """Raw is Fahrenheit (~97-99). Body temperature in C must land near 37."""
    t = ds.vitals()["temp_c"].dropna()
    assert len(t) > 0
    assert 30.0 < t.median() < 42.0, f"median {t.median():.1f} - looks like Fahrenheit"


def test_repeated_observations_exist(ds):
    """The whole trajectory thesis depends on this being true."""
    n = ds.vitals().groupby("stay_id").size()
    assert (n >= 2).mean() > 0.5, "most stays must have >=2 readings"


def test_patients_have_multiple_stays(ds):
    """Why splits must group on patient_id, not stay_id. Observed: 3.5 stays/patient."""
    stays = ds.stays()
    assert stays["stay_id"].nunique() > stays["patient_id"].nunique()


def test_acuity_is_present_but_is_not_a_feature(ds):
    """Acuity is loaded for evaluation only - it must never reach the vitals frame."""
    assert "acuity" in ds.triage().columns
    assert "acuity" not in ds.vitals().columns


class TestMixedUnitTemperature:
    """MIMIC-ED charts temperature in mixed units. Converting the whole column
    turns a normal 36.8 C into 2.7 C, which then reads as a data error and is
    discarded - silently deleting the most normal-looking readings."""

    def test_celsius_values_pass_through(self):
        from vigil.data.mimic import to_celsius
        assert to_celsius(pd.Series([36.8, 34.8, 31.4])).round(1).tolist() == [36.8, 34.8, 31.4]

    def test_fahrenheit_values_convert(self):
        from vigil.data.mimic import to_celsius
        assert to_celsius(pd.Series([98.6, 104.0])).round(1).tolist() == [37.0, 40.0]

    def test_values_in_neither_range_become_null(self):
        from vigil.data.mimic import to_celsius
        out = to_celsius(pd.Series([200.0, 60.0, 0.0]))
        assert out.isna().all()

    def test_the_two_ranges_cannot_overlap(self):
        """45 C < 77 F, so per-value detection is unambiguous, not a guess."""
        from vigil.data.mimic import _C_RANGE, _F_RANGE
        assert _C_RANGE[1] < _F_RANGE[0]

    def test_no_real_temperature_is_discarded(self, ds):
        ds.vitals()
        assert ds.plausibility_report["temp_c"] == 0

    def test_loaded_temperatures_are_physiological(self, ds):
        t = ds.vitals()["temp_c"].dropna()
        assert 35.0 < t.median() < 38.0
        assert t.min() > 25.0 and t.max() < 45.0


class TestTriageAsFirstObservation:
    """Triage vitals are vital signs; they are just kept in another table.
    Leaving them out delays every stay's first observation and biases early
    landmarks toward patients who happened to be re-checked quickly."""

    def test_including_triage_adds_one_observation_per_stay(self, ds):
        without = len(ds.vitals(include_triage=False))
        with_ = len(ds.vitals(include_triage=True))
        assert with_ - without == ds.stays()["stay_id"].nunique()

    def test_triage_observation_lands_at_arrival(self, ds):
        stays = ds.stays().set_index("stay_id")["intime"]
        v = ds.vitals(include_triage=True)
        first = v.groupby("stay_id")["charttime"].min()
        aligned = (first == stays.reindex(first.index)).mean()
        assert aligned > 0.9, "the t=0 observation should sit at intime"

    def test_ordering_contract_still_holds_after_prepending(self, ds):
        v = ds.vitals(include_triage=True)
        ordered = v.groupby("stay_id")["charttime"].apply(lambda s: s.is_monotonic_increasing)
        assert ordered.all()

    def test_it_removes_the_empty_landmark_filter_entirely(self, ds):
        """The measurable point of the change: a 20% biasing filter goes to zero."""
        from vigil.data.landmarks import LandmarkSpec, build_landmarks

        spec = LandmarkSpec(grid_minutes=(30, 60, 90, 120, 180, 240, 360))
        stays = ds.stays()
        _, without = build_landmarks(stays, ds.vitals(include_triage=False), spec)
        _, with_ = build_landmarks(stays, ds.vitals(include_triage=True), spec)
        assert without.reasons.get("no observation yet at landmark", 0) > 0
        assert with_.reasons.get("no observation yet at landmark", 0) == 0
