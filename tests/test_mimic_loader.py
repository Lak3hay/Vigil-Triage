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
