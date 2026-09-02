"""The canonical schema every dataset loader must produce.

Loaders normalise their source into these frames so that nothing downstream
knows or cares which dataset it is looking at. Swapping MIMIC for NHAMCS is a
config change, not a rewrite.
"""
from __future__ import annotations

import pandas as pd

#: One row per ED stay.
STAYS = ["stay_id", "patient_id", "intime", "outtime", "disposition"]

#: One row per stay: the first recorded vitals, complaint, and the nurse's acuity.
#: `acuity` is here for EVALUATION ONLY. It is never a training target
#: (see PLAN.md 4.2 - training on it inherits the blind spots we exist to catch).
TRIAGE = ["stay_id", "patient_id", "acuity", "chief_complaint", "pain"]

#: Many rows per stay: repeated timestamped observations. This table is what
#: makes the trajectory model possible at all.
VITALS = ["stay_id", "patient_id", "charttime", "hr", "rr", "spo2", "sbp", "dbp", "temp_c"]

VITAL_COLS = ["hr", "rr", "spo2", "sbp", "dbp", "temp_c"]

#: Physiologically plausible bounds. Values outside become NaN rather than being
#: clipped: an impossible reading is absence of information, not extreme information.
#: The demo data contains a 31.4 F temperature, which is why this exists.
PLAUSIBLE: dict[str, tuple[float, float]] = {
    "hr": (10.0, 300.0),
    "rr": (2.0, 80.0),
    "spo2": (30.0, 100.0),
    "sbp": (30.0, 300.0),
    "dbp": (10.0, 200.0),
    "temp_c": (25.0, 45.0),
}


class SchemaError(ValueError):
    """Raised when a loader emits a frame that violates the canonical contract."""


def _require(df: pd.DataFrame, cols: list[str], name: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise SchemaError(f"{name}: missing required columns {missing}")


def apply_plausibility(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Null out physiologically impossible values. Returns (frame, per-column counts).

    The counts are returned rather than logged because every filter's firing rate
    has to be reportable (PLAN.md 15.3). A filter nobody counts is a filter nobody
    can defend.
    """
    out = df.copy()
    dropped: dict[str, int] = {}
    for col, (lo, hi) in PLAUSIBLE.items():
        if col not in out.columns:
            continue
        bad = out[col].notna() & ((out[col] < lo) | (out[col] > hi))
        dropped[col] = int(bad.sum())
        out.loc[bad, col] = pd.NA
    return out, dropped


def validate_stays(df: pd.DataFrame) -> pd.DataFrame:
    _require(df, STAYS, "stays")
    if df["stay_id"].duplicated().any():
        raise SchemaError("stays: stay_id must be unique")
    return df


def validate_triage(df: pd.DataFrame) -> pd.DataFrame:
    _require(df, TRIAGE, "triage")
    if df["stay_id"].duplicated().any():
        raise SchemaError("triage: one row per stay_id")
    return df


def validate_vitals(df: pd.DataFrame) -> pd.DataFrame:
    """Validate the vitals frame, including the ordering guarantee.

    MIMIC's vitalsign table is NOT stored in chronological order. Any code that
    takes `.iloc[-1]` as "the latest reading" on the raw table is silently wrong,
    so sortedness is a contract the loader must satisfy, not an assumption.
    """
    _require(df, VITALS, "vitals")
    if not pd.api.types.is_datetime64_any_dtype(df["charttime"]):
        raise SchemaError("vitals: charttime must be datetime64")
    if len(df) > 1:
        ordered = df.groupby("stay_id", sort=False)["charttime"].apply(
            lambda s: s.is_monotonic_increasing
        )
        if not ordered.all():
            n = int((~ordered).sum())
            raise SchemaError(
                f"vitals: charttime not sorted within {n} stay(s). "
                "Loaders must sort - MIMIC's raw table is unordered."
            )
    return df
