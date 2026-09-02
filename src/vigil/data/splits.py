"""Train/validation/test splitting.

Two rules, both easy to get wrong and both fatal (PLAN.md 4):

1. **Group on patient, never on stay.** Observed in MIMIC-IV-ED: 3.5 stays per
   patient, one patient with 23. Splitting on ``stay_id`` puts the same person on
   both sides of the wall and manufactures performance out of nothing.
2. **Temporal, never random.** Random splits leak across time and inflate every
   metric.

Rule 2 has a complication specific to this dataset, described in
:func:`make_splits` -- it is currently unsatisfiable from the ED module alone, and
this module refuses rather than pretending otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

SPLIT_NAMES = ("train", "val", "test")


class SplitError(ValueError):
    pass


class PatientOverlapError(AssertionError):
    """Raised when the same patient appears in more than one split."""


@dataclass(frozen=True)
class SplitSpec:
    """
    Parameters
    ----------
    strategy
        ``"patient_temporal"`` -- group on patient, order by ``anchor``. The
        correct choice, and it requires an anchor (see :func:`make_splits`).

        ``"patient_random"`` -- group on patient, assign at random. **Interim
        only.** Honest about leaking across time; use while the anchor is
        unavailable, and say so beside any number produced with it.
    fractions
        (train, val, test). Must sum to 1.
    seed
        Only meaningful for ``patient_random``.
    """

    strategy: str = "patient_temporal"
    fractions: tuple[float, float, float] = (0.70, 0.15, 0.15)
    seed: int = 42

    def __post_init__(self) -> None:
        if self.strategy not in {"patient_temporal", "patient_random"}:
            raise SplitError(f"unknown strategy {self.strategy!r}")
        if abs(sum(self.fractions) - 1.0) > 1e-9:
            raise SplitError(f"fractions must sum to 1, got {sum(self.fractions)}")
        if any(f < 0 for f in self.fractions):
            raise SplitError("fractions must be non-negative")


def _cut_points(n: int, fractions: tuple[float, float, float]) -> tuple[int, int]:
    n_train = int(round(n * fractions[0]))
    n_val = int(round(n * fractions[1]))
    return n_train, n_train + n_val


def make_splits(
    stays: pd.DataFrame,
    spec: SplitSpec | None = None,
    anchor: pd.Series | None = None,
) -> pd.DataFrame:
    """Assign every patient to exactly one split.

    Parameters
    ----------
    stays
        Canonical stays frame; only ``patient_id`` is required.
    anchor
        Required by ``patient_temporal``. Indexed by ``patient_id``, holding an
        orderable value that places the patient in real time -- for MIMIC-IV that
        is ``patients.anchor_year_group``.

        **Why this cannot be derived from the ED module:** MIMIC de-identifies
        dates by shifting each patient independently into the future, so
        ``intime`` is internally consistent within a patient and meaningless
        across patients. The demo subset spans 2112-2201 for exactly this reason.
        Sorting patients by ``intime`` therefore sorts them by a random offset,
        which produces a split that *looks* temporal and is not. Passing no
        anchor raises rather than silently doing that.

        *Verify the anchor field against current MIMIC documentation before
        relying on it (PLAN.md 4, rule 3).*

    Returns
    -------
    DataFrame
        ``patient_id`` -> ``split``, one row per patient.
    """
    spec = spec or SplitSpec()
    patients = pd.Index(pd.unique(stays["patient_id"])).sort_values()
    if len(patients) < len(SPLIT_NAMES):
        raise SplitError(f"need at least {len(SPLIT_NAMES)} patients, got {len(patients)}")

    if spec.strategy == "patient_temporal":
        if anchor is None:
            raise SplitError(
                "patient_temporal needs an `anchor` (patient_id -> orderable time). "
                "MIMIC shifts dates per patient, so `intime` cannot supply it -- use "
                "patients.anchor_year_group from the MIMIC-IV hospital module. "
                "Until that is available use SplitSpec(strategy='patient_random') "
                "and report that the split is not temporal."
            )
        missing = patients.difference(anchor.index)
        if len(missing):
            raise SplitError(f"anchor missing for {len(missing)} patient(s), e.g. {list(missing[:3])}")
        # Stable ordering: anchor first, patient_id to break ties reproducibly.
        order = (
            pd.DataFrame({"patient_id": patients, "anchor": anchor.reindex(patients).to_numpy()})
            .sort_values(["anchor", "patient_id"], kind="mergesort")["patient_id"]
            .to_numpy()
        )
    else:
        order = patients.to_numpy().copy()
        np.random.default_rng(spec.seed).shuffle(order)

    i, j = _cut_points(len(order), spec.fractions)
    labels = np.empty(len(order), dtype=object)
    labels[:i] = "train"
    labels[i:j] = "val"
    labels[j:] = "test"

    out = pd.DataFrame({"patient_id": order, "split": labels})
    assert_no_patient_overlap(out)
    return out.sort_values("patient_id", kind="mergesort").reset_index(drop=True)


def assert_no_patient_overlap(splits: pd.DataFrame) -> None:
    """Fail loudly if any patient landed in more than one split.

    Cheap, and it guards the failure mode that invalidates every downstream
    number without changing how any of them look.
    """
    dupes = splits["patient_id"].duplicated()
    if dupes.any():
        offenders = splits.loc[dupes, "patient_id"].unique()
        raise PatientOverlapError(
            f"{len(offenders)} patient(s) in more than one split, e.g. {list(offenders[:5])}"
        )


def apply_splits(df: pd.DataFrame, splits: pd.DataFrame) -> pd.DataFrame:
    """Attach a ``split`` column to any frame carrying ``patient_id``."""
    if "patient_id" not in df.columns:
        raise SplitError("frame has no patient_id; splits are defined on patients")
    out = df.merge(splits, on="patient_id", how="left")
    if out["split"].isna().any():
        n = int(out["split"].isna().sum())
        raise SplitError(f"{n} row(s) belong to patients absent from the split table")
    return out


def split_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Rows, stays and patients per split -- the sanity table to eyeball."""
    g = df.groupby("split", dropna=False)
    out = pd.DataFrame({
        "rows": g.size(),
        "stays": g["stay_id"].nunique() if "stay_id" in df.columns else g.size(),
        "patients": g["patient_id"].nunique(),
    })
    out["pct_patients"] = (100.0 * out["patients"] / out["patients"].sum()).round(1)
    return out.reindex([s for s in SPLIT_NAMES if s in out.index])
