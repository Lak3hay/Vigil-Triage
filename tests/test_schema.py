"""Schema contract tests.

Each test here corresponds to a trap actually observed in the MIMIC-IV-ED demo
data, not to a hypothetical one. See PLAN.md 15.1: the check must be able to fail.
"""
import pandas as pd
import pytest

from vigil.data import schema


def _vitals(charttimes, stay_id=1, **cols):
    n = len(charttimes)
    base = dict(hr=[80.0] * n, rr=[16.0] * n, spo2=[98.0] * n,
                sbp=[120.0] * n, dbp=[70.0] * n, temp_c=[37.0] * n)
    base.update(cols)
    return pd.DataFrame({
        "stay_id": [stay_id] * n,
        "patient_id": [10] * n,
        "charttime": pd.to_datetime(charttimes),
        **base,
    })


class TestOrderingContract:
    """MIMIC's vitalsign table is not chronologically ordered. Observed, not assumed."""

    def test_sorted_vitals_pass(self):
        df = _vitals(["2150-01-01 10:00", "2150-01-01 10:30", "2150-01-01 11:00"])
        assert schema.validate_vitals(df) is df

    def test_unsorted_vitals_rejected(self):
        # This is the exact shape of the raw table: 03:27 before 00:08.
        df = _vitals(["2150-01-01 03:27", "2150-01-01 00:08", "2150-01-01 09:25"])
        with pytest.raises(schema.SchemaError, match="not sorted"):
            schema.validate_vitals(df)

    def test_ordering_checked_per_stay_not_globally(self):
        """Two stays each internally sorted is valid even if concatenation is not."""
        a = _vitals(["2150-01-01 10:00", "2150-01-01 11:00"], stay_id=1)
        b = _vitals(["2149-01-01 10:00", "2149-01-01 11:00"], stay_id=2)
        schema.validate_vitals(pd.concat([a, b], ignore_index=True))

    def test_charttime_must_be_datetime(self):
        df = _vitals(["2150-01-01 10:00"])
        df["charttime"] = df["charttime"].astype(str)
        with pytest.raises(schema.SchemaError, match="datetime"):
            schema.validate_vitals(df)


class TestPlausibility:
    def test_impossible_temperature_becomes_null(self):
        """31.4 F -> -0.3 C appears in the real demo data. It is not cold, it is wrong."""
        df = _vitals(["2150-01-01 10:00"] * 2, temp_c=[37.0, -0.3])
        out, report = schema.apply_plausibility(df)
        assert out["temp_c"].tolist()[0] == 37.0
        assert pd.isna(out["temp_c"].tolist()[1])
        assert report["temp_c"] == 1

    def test_impossible_values_are_nulled_not_clipped(self):
        """An impossible reading is absence of information, not extreme information."""
        df = _vitals(["2150-01-01 10:00"], hr=[999.0])
        out, _ = schema.apply_plausibility(df)
        assert pd.isna(out["hr"].iloc[0]), "must be NaN, not clipped to the bound"

    def test_genuine_extremes_survive(self):
        """A real HR of 190 is a sick patient, not a data error. Do not filter it."""
        df = _vitals(["2150-01-01 10:00"], hr=[190.0], spo2=[71.0])
        out, report = schema.apply_plausibility(df)
        assert out["hr"].iloc[0] == 190.0
        assert out["spo2"].iloc[0] == 71.0
        assert report["hr"] == 0 and report["spo2"] == 0

    def test_report_counts_every_column(self):
        df = _vitals(["2150-01-01 10:00"])
        _, report = schema.apply_plausibility(df)
        assert set(report) == set(schema.PLAUSIBLE), "every filter must report a firing rate"


class TestUniqueness:
    def test_duplicate_stay_rejected(self):
        df = pd.DataFrame({"stay_id": [1, 1], "patient_id": [9, 9],
                           "intime": pd.to_datetime(["2150-01-01"] * 2),
                           "outtime": pd.to_datetime(["2150-01-02"] * 2),
                           "disposition": ["HOME"] * 2})
        with pytest.raises(schema.SchemaError, match="unique"):
            schema.validate_stays(df)
