"""Landmark table construction.

A landmark is one prediction moment: *stay S, t minutes after arrival*. Features
may use everything observed in ``[0, t]`` and nothing after. Getting that wrong is
the single most likely silent killer in this project (PLAN.md R4), because a leaked
feature produces excellent, plausible, worthless numbers.

So leakage is made structurally hard rather than left to discipline: feature code
never touches the raw vitals table. It receives a *visible slice* from
:func:`expand_visible` or :func:`attach_last_observation`, both of which assert the
cutoff before returning.

Boundary convention, stated once and applied everywhere: an observation charted
exactly *at* the landmark is **visible**. "What was known by minute t" is inclusive.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from vigil.data.exclusions import ExclusionReport

LANDMARKS = ["landmark_id", "stay_id", "patient_id", "t_min", "landmark_ts", "n_obs_before"]


class LeakageError(AssertionError):
    """Raised when a frame contains an observation later than its landmark."""


@dataclass(frozen=True)
class LandmarkSpec:
    """How to lay landmarks down on a stay.

    Parameters
    ----------
    grid_minutes
        Minutes after arrival at which a prediction is made.
    horizon_minutes
        The prediction window ``(t, t + horizon]``. Recorded here so it travels
        with the landmark table instead of being reintroduced by hand later.
    require_prior_observation
        Drop landmarks with nothing observed yet. On by default -- a landmark with
        no data is not a prediction, it is a guess from the prior. **This is a
        filter and its firing rate is reported** (PLAN.md 3.2).
    """

    grid_minutes: tuple[int, ...] = (30, 60, 90, 120, 180, 240)
    horizon_minutes: int = 60
    require_prior_observation: bool = True

    def __post_init__(self) -> None:
        if not self.grid_minutes:
            raise ValueError("grid_minutes must not be empty")
        if list(self.grid_minutes) != sorted(self.grid_minutes):
            raise ValueError("grid_minutes must be ascending")
        if min(self.grid_minutes) < 0:
            raise ValueError("grid_minutes must be non-negative")


def _counts_before(vitals: pd.DataFrame, landmarks: pd.DataFrame) -> np.ndarray:
    """Observations charted at or before each landmark.

    Relies on the schema's guarantee that vitals are sorted by charttime within
    each stay (``schema.validate_vitals``) -- that contract is what makes a
    binary search correct here instead of a full scan.
    """
    times: dict[object, np.ndarray] = {
        sid: g.to_numpy() for sid, g in vitals.groupby("stay_id", sort=False)["charttime"]
    }
    out = np.zeros(len(landmarks), dtype=np.int64)
    for i, (sid, ts) in enumerate(
        zip(landmarks["stay_id"].to_numpy(), landmarks["landmark_ts"].to_numpy(),
            strict=True)
    ):
        arr = times.get(sid)
        if arr is not None:
            out[i] = np.searchsorted(arr, ts, side="right")  # side="right" => inclusive
    return out


def build_landmarks(
    stays: pd.DataFrame,
    vitals: pd.DataFrame,
    spec: LandmarkSpec | None = None,
) -> tuple[pd.DataFrame, ExclusionReport]:
    """Lay the landmark grid over every stay.

    A landmark exists only while the patient is still present: ``landmark_ts <
    outtime``. A landmark after discharge is not a prediction anyone could have
    made.

    Returns
    -------
    (landmarks, report)
        ``landmarks`` has one row per prediction moment, columns :data:`LANDMARKS`.
        ``report`` carries the firing rate of every filter applied.
    """
    spec = spec or LandmarkSpec()
    rep = ExclusionReport(stage="build_landmarks", unit="landmark")

    grid = np.asarray(spec.grid_minutes, dtype="int64")
    n_stays, n_grid = len(stays), len(grid)
    rep.n_input = n_stays * n_grid

    lm = pd.DataFrame({
        "stay_id": np.repeat(stays["stay_id"].to_numpy(), n_grid),
        "patient_id": np.repeat(stays["patient_id"].to_numpy(), n_grid),
        "t_min": np.tile(grid, n_stays),
        "intime": np.repeat(stays["intime"].to_numpy(), n_grid),
        "outtime": np.repeat(stays["outtime"].to_numpy(), n_grid),
    })
    lm["landmark_ts"] = lm["intime"] + pd.to_timedelta(lm["t_min"], unit="m")

    # The patient must still be here. NaT outtime is treated as still present.
    after_discharge = lm["outtime"].notna() & (lm["landmark_ts"] >= lm["outtime"])
    rep.drop(lm.loc[after_discharge, "stay_id"].to_numpy(), "landmark at or after discharge")
    lm = lm.loc[~after_discharge].copy()

    lm["n_obs_before"] = _counts_before(vitals, lm)

    if spec.require_prior_observation:
        empty = lm["n_obs_before"] == 0
        rep.drop(lm.loc[empty, "stay_id"].to_numpy(), "no observation yet at landmark")
        lm = lm.loc[~empty].copy()

    lm = lm.sort_values(["stay_id", "t_min"], kind="mergesort").reset_index(drop=True)
    lm.insert(0, "landmark_id", np.arange(len(lm), dtype=np.int64))
    rep.n_output = len(lm)
    return lm[LANDMARKS], rep


# ── leakage-safe accessors ────────────────────────────────────────────────────
# Feature code uses these. It never reads the raw vitals table directly.

def _assert_no_leakage(df: pd.DataFrame, obs_col: str, cut_col: str) -> None:
    if df.empty:
        return
    bad = df[obs_col] > df[cut_col]
    if bad.any():
        i = df.index[bad][0]
        raise LeakageError(
            f"{int(bad.sum())} observation(s) later than their landmark; "
            f"first at row {i}: {df.loc[i, obs_col]} > {df.loc[i, cut_col]}"
        )


def expand_visible(
    landmarks: pd.DataFrame,
    vitals: pd.DataFrame,
    window_minutes: int | None = None,
) -> pd.DataFrame:
    """Every observation visible at each landmark, as a long frame.

    One row per (landmark, observation). This is what trajectory features consume.

    Parameters
    ----------
    window_minutes
        Keep only observations within the last ``window_minutes`` before the
        landmark. ``None`` keeps the whole history.

    Notes
    -----
    Size is (landmarks x observations per stay), so it grows faster than either
    input. Pass ``window_minutes`` on the full dataset.
    """
    merged = landmarks.merge(
        vitals.drop(columns=["patient_id"], errors="ignore"), on="stay_id", how="inner"
    )
    merged = merged.loc[merged["charttime"] <= merged["landmark_ts"]]

    if window_minutes is not None:
        floor = merged["landmark_ts"] - pd.Timedelta(minutes=window_minutes)
        merged = merged.loc[merged["charttime"] >= floor]

    merged = merged.copy()
    merged["mins_before"] = (
        merged["landmark_ts"] - merged["charttime"]
    ).dt.total_seconds() / 60.0

    _assert_no_leakage(merged, "charttime", "landmark_ts")
    return merged.sort_values(["landmark_id", "charttime"], kind="mergesort").reset_index(
        drop=True
    )


def attach_last_observation(
    landmarks: pd.DataFrame,
    vitals: pd.DataFrame,
    value_cols: list[str] | None = None,
) -> pd.DataFrame:
    """The most recent observation at or before each landmark (the B0 view).

    Carries ``mins_since_obs`` because staleness is itself informative -- a value
    from four hours ago is not the same evidence as the same value from four
    minutes ago (PLAN.md 5.1).
    """
    from vigil.data.schema import VITAL_COLS

    cols = value_cols or VITAL_COLS
    left = landmarks.sort_values("landmark_ts", kind="mergesort")
    right = vitals.sort_values("charttime", kind="mergesort")

    out = pd.merge_asof(
        left,
        right[["stay_id", "charttime", *cols]],
        left_on="landmark_ts",
        right_on="charttime",
        by="stay_id",
        direction="backward",
        allow_exact_matches=True,  # inclusive boundary, per module docstring
    )
    out["mins_since_obs"] = (
        out["landmark_ts"] - out["charttime"]
    ).dt.total_seconds() / 60.0

    _assert_no_leakage(out.dropna(subset=["charttime"]), "charttime", "landmark_ts")
    return out.sort_values("landmark_id", kind="mergesort").reset_index(drop=True)
