"""Landmark construction tests.

The leading cases are hand-computable: a two-hour stay on a 30-minute grid, where
the correct answer is obvious by inspection and the test cannot quietly agree with
a broken implementation.
"""
import pandas as pd
import pytest

from vigil.data.landmarks import (
    LandmarkSpec,
    LeakageError,
    attach_last_observation,
    build_landmarks,
    expand_visible,
)

T0 = pd.Timestamp("2150-01-01 10:00")


def _stay(stay_id=1, patient_id=100, hours=2.0):
    return pd.DataFrame({
        "stay_id": [stay_id], "patient_id": [patient_id],
        "intime": [T0], "outtime": [T0 + pd.Timedelta(hours=hours)],
        "disposition": ["HOME"],
    })


def _vitals(offsets_min, stay_id=1, patient_id=100, hr=None):
    n = len(offsets_min)
    return pd.DataFrame({
        "stay_id": [stay_id] * n, "patient_id": [patient_id] * n,
        "charttime": [T0 + pd.Timedelta(minutes=m) for m in offsets_min],
        "hr": hr if hr is not None else [80.0] * n,
        "rr": [16.0] * n, "spo2": [98.0] * n,
        "sbp": [120.0] * n, "dbp": [70.0] * n, "temp_c": [37.0] * n,
    })


class TestHandComputable:
    """Stay 10:00-12:00, grid every 30 min. By inspection: 10:30, 11:00, 11:30."""

    SPEC = LandmarkSpec(grid_minutes=(30, 60, 90, 120, 180), require_prior_observation=False)

    def test_landmarks_inside_the_stay_only(self):
        lm, _ = build_landmarks(_stay(), _vitals([0]), self.SPEC)
        assert lm["t_min"].tolist() == [30, 60, 90]

    def test_landmark_exactly_at_discharge_is_excluded(self):
        """At t=120 the patient is leaving; no prediction was possible."""
        lm, rep = build_landmarks(_stay(), _vitals([0]), self.SPEC)
        assert 120 not in lm["t_min"].tolist()
        assert rep.reasons["landmark at or after discharge"] == 2  # t=120 and t=180

    def test_landmark_timestamps_are_arrival_plus_offset(self):
        lm, _ = build_landmarks(_stay(), _vitals([0]), self.SPEC)
        assert lm["landmark_ts"].tolist() == [
            pd.Timestamp("2150-01-01 10:30"),
            pd.Timestamp("2150-01-01 11:00"),
            pd.Timestamp("2150-01-01 11:30"),
        ]

    def test_observation_counts_are_cumulative(self):
        """Observations at 0, 45, 100 min -> counts 1, 2, 2 at t = 30, 60, 90."""
        lm, _ = build_landmarks(_stay(), _vitals([0, 45, 100]), self.SPEC)
        assert lm["n_obs_before"].tolist() == [1, 2, 2]

    def test_still_present_when_outtime_is_missing(self):
        s = _stay()
        s["outtime"] = pd.NaT
        lm, _ = build_landmarks(s, _vitals([0]), self.SPEC)
        assert lm["t_min"].tolist() == [30, 60, 90, 120, 180]


class TestBoundaryConvention:
    """An observation charted exactly at the landmark is visible. Stated once, tested here."""

    SPEC = LandmarkSpec(grid_minutes=(30,), require_prior_observation=False)

    def test_observation_at_the_landmark_counts(self):
        lm, _ = build_landmarks(_stay(), _vitals([30]), self.SPEC)
        assert lm["n_obs_before"].iloc[0] == 1

    def test_observation_one_minute_later_does_not(self):
        lm, _ = build_landmarks(_stay(), _vitals([31]), self.SPEC)
        assert lm["n_obs_before"].iloc[0] == 0

    def test_expand_visible_includes_the_boundary_row(self):
        lm, _ = build_landmarks(_stay(), _vitals([30]), self.SPEC)
        vis = expand_visible(lm, _vitals([30]))
        assert len(vis) == 1 and vis["mins_before"].iloc[0] == 0.0


class TestLeakage:
    """R4. The detector must be shown to fire, or it is decoration."""

    SPEC = LandmarkSpec(grid_minutes=(30, 60), require_prior_observation=False)

    def test_future_observations_never_appear(self):
        v = _vitals([0, 20, 50, 200])
        lm, _ = build_landmarks(_stay(), v, self.SPEC)
        vis = expand_visible(lm, v)
        assert (vis["charttime"] <= vis["landmark_ts"]).all()
        assert (vis["mins_before"] >= 0).all()

    def test_visible_set_grows_with_the_landmark(self):
        """t=30 sees {0,20}; t=60 sees {0,20,50}. Hand-checkable."""
        v = _vitals([0, 20, 50, 200])
        lm, _ = build_landmarks(_stay(), v, self.SPEC)
        vis = expand_visible(lm, v)
        assert vis.groupby("t_min").size().tolist() == [2, 3]

    def test_detector_fires_on_a_planted_violation(self):
        """Falsification: break the invariant on purpose and confirm it is caught."""
        v = _vitals([0])
        lm, _ = build_landmarks(_stay(), v, self.SPEC)
        bad = lm.merge(v.drop(columns="patient_id"), on="stay_id")
        bad["charttime"] = bad["landmark_ts"] + pd.Timedelta(minutes=1)
        from vigil.data.landmarks import _assert_no_leakage

        with pytest.raises(LeakageError, match="later than their landmark"):
            _assert_no_leakage(bad, "charttime", "landmark_ts")

    def test_window_limits_history(self):
        """A 30-minute window at t=60 sees only the 50-minute observation."""
        v = _vitals([0, 20, 50])
        lm, _ = build_landmarks(_stay(), v, LandmarkSpec((60,), require_prior_observation=False))
        vis = expand_visible(lm, v, window_minutes=30)
        assert vis["mins_before"].tolist() == [10.0]


class TestRequirePriorObservation:
    def test_landmarks_without_data_are_dropped_and_counted(self):
        """First observation at 45 min, so t=30 has nothing and must go."""
        spec = LandmarkSpec(grid_minutes=(30, 60), require_prior_observation=True)
        lm, rep = build_landmarks(_stay(), _vitals([45]), spec)
        assert lm["t_min"].tolist() == [60]
        assert rep.reasons["no observation yet at landmark"] == 1

    def test_report_states_its_own_denominator(self):
        spec = LandmarkSpec(grid_minutes=(30, 60), require_prior_observation=True)
        _, rep = build_landmarks(_stay(), _vitals([45]), spec)
        assert rep.n_input == 2 and rep.n_output == 1
        assert "%" in str(rep)


class TestLastObservation:
    SPEC = LandmarkSpec(grid_minutes=(30, 60), require_prior_observation=False)

    def test_picks_the_most_recent_visible_row(self):
        v = _vitals([0, 20, 50], hr=[80.0, 95.0, 110.0])
        lm, _ = build_landmarks(_stay(), v, self.SPEC)
        out = attach_last_observation(lm, v)
        assert out["hr"].tolist() == [95.0, 110.0]  # t=30 -> the 20-min row; t=60 -> 50-min

    def test_staleness_is_recorded(self):
        v = _vitals([0, 20, 50])
        lm, _ = build_landmarks(_stay(), v, self.SPEC)
        out = attach_last_observation(lm, v)
        assert out["mins_since_obs"].tolist() == [10.0, 10.0]

    def test_no_prior_observation_yields_null_not_a_future_value(self):
        v = _vitals([45], hr=[123.0])
        lm, _ = build_landmarks(_stay(), v, self.SPEC)
        out = attach_last_observation(lm, v)
        assert pd.isna(out.loc[out.t_min == 30, "hr"].iloc[0]), "must not reach forward"
        assert out.loc[out.t_min == 60, "hr"].iloc[0] == 123.0


class TestMultipleStays:
    def test_stays_do_not_bleed_into_each_other(self):
        spec = LandmarkSpec(grid_minutes=(30,), require_prior_observation=False)
        stays = pd.concat([_stay(1, 100), _stay(2, 200)], ignore_index=True)
        v = pd.concat([_vitals([0, 10], 1, 100, hr=[70.0, 71.0]),
                       _vitals([0, 10], 2, 200, hr=[90.0, 91.0])], ignore_index=True)
        lm, _ = build_landmarks(stays, v, spec)
        out = attach_last_observation(lm, v)
        assert out.set_index("stay_id")["hr"].to_dict() == {1: 71.0, 2: 91.0}

    def test_landmark_ids_are_unique(self):
        spec = LandmarkSpec(grid_minutes=(30, 60), require_prior_observation=False)
        stays = pd.concat([_stay(1, 100), _stay(2, 200)], ignore_index=True)
        v = pd.concat([_vitals([0], 1, 100), _vitals([0], 2, 200)], ignore_index=True)
        lm, _ = build_landmarks(stays, v, spec)
        assert lm["landmark_id"].is_unique and len(lm) == 4


class TestSpecValidation:
    @pytest.mark.parametrize("grid", [(), (60, 30), (-30, 60)])
    def test_bad_grids_rejected(self, grid):
        with pytest.raises(ValueError):
            LandmarkSpec(grid_minutes=grid)
