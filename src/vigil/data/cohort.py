"""Cohort construction.

Which ED stays are eligible for the study, and -- more importantly -- an account of
every stay that is not, with its reason.

Two commitments shape this module:

**A constraint that cannot be satisfied must refuse, never silently vanish.** Asking
for an adult-only cohort when the age column is absent does not quietly give you
everyone; it raises. The same rule governs the temporal split
(:mod:`vigil.data.splits`) and the NHAMCS loader.

**Leaving without being seen is not a negative outcome.** LWBS, eloped and
against-medical-advice stays are *censored*: we stopped observing them, which is
not the same as watching them stay well. Excluding them would quietly delete a
group enriched for long waits -- the exact population the project is about -- so
they are retained and flagged for the label builder to censor properly.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from vigil.data.exclusions import ExclusionReport

#: Dispositions where observation stops for a reason unrelated to recovery.
CENSORING_DISPOSITIONS = ("LEFT WITHOUT BEING SEEN", "ELOPED", "LEFT AGAINST MEDICAL ADVICE")


class CohortError(ValueError):
    pass


@dataclass(frozen=True)
class CohortSpec:
    """Eligibility rules.

    Parameters
    ----------
    min_age, max_age
        Age bounds. **Requires an `age` column** -- MIMIC-IV-ED's `edstays` has
        none, so this needs `patients.anchor_age` from the hospital module. Leave
        as ``None`` until that is available; setting it without the column raises.
        Pediatric physiology genuinely differs (compensated shock, different vital
        ranges, PEWS rather than NEWS2), so one model spanning both ages is a
        modelling decision, not a default.
    min_stay_minutes
        A stay shorter than the first landmark contributes no prediction moment.
        Structural, not a judgement call.
    max_stay_hours
        Upper bound on plausible ED length of stay; beyond it the record is
        usually an administrative artifact. ``None`` disables.
    require_any_observation
        Drop stays with no vitals at all.

        ⚠️ **This is the one exclusion here that can bias the result.** Being
        measured at all correlates with being attended to, so dropping the
        unmeasured selects toward patients somebody looked at. It is on by
        default because a stay with no observations cannot produce a feature
        vector, but its firing rate is reported and should be checked against the
        outcome before any result is believed (PLAN.md 3.2).
    """

    min_age: int | None = None
    max_age: int | None = None
    min_stay_minutes: int = 30
    max_stay_hours: float | None = 72.0
    require_any_observation: bool = True

    def __post_init__(self) -> None:
        if self.min_stay_minutes < 0:
            raise CohortError("min_stay_minutes must be non-negative")
        if self.max_stay_hours is not None and self.max_stay_hours <= 0:
            raise CohortError("max_stay_hours must be positive")
        if (
            self.min_age is not None
            and self.max_age is not None
            and self.min_age > self.max_age
        ):
            raise CohortError("min_age must not exceed max_age")


def build_cohort(
    stays: pd.DataFrame,
    vitals: pd.DataFrame,
    spec: CohortSpec | None = None,
) -> tuple[pd.DataFrame, ExclusionReport]:
    """Apply eligibility rules, accounting for every stay removed.

    Returns
    -------
    (cohort, report)
        ``cohort`` is the eligible subset of ``stays`` plus two derived columns:
        ``stay_minutes`` and ``is_censored`` (see :data:`CENSORING_DISPOSITIONS`).
        ``report`` carries each filter's firing rate and the identities it removed,
        so :meth:`ExclusionReport.stratify` can audit it once labels exist.
    """
    spec = spec or CohortSpec()
    rep = ExclusionReport(stage="build_cohort", unit="stay", n_input=len(stays))

    df = stays.copy()
    df["stay_minutes"] = (df["outtime"] - df["intime"]).dt.total_seconds() / 60.0

    def _drop(mask: pd.Series, reason: str) -> None:
        nonlocal df
        mask = mask.fillna(False)
        if mask.any():
            rep.drop(df.loc[mask, "stay_id"].to_numpy(), reason)
            df = df.loc[~mask]

    # -- age ------------------------------------------------------------------
    wants_age = spec.min_age is not None or spec.max_age is not None
    if wants_age:
        if "age" not in df.columns:
            raise CohortError(
                "CohortSpec requests an age bound but `stays` has no `age` column. "
                "MIMIC-IV-ED's edstays does not carry age -- it needs "
                "patients.anchor_age from the MIMIC-IV hospital module. Leave "
                "min_age/max_age as None until that is joined; do not assume an "
                "age filter silently applied."
            )
        if spec.min_age is not None:
            _drop(df["age"] < spec.min_age, f"age below {spec.min_age}")
        if spec.max_age is not None:
            _drop(df["age"] > spec.max_age, f"age above {spec.max_age}")

    # -- duration -------------------------------------------------------------
    _drop(df["stay_minutes"].notna() & (df["stay_minutes"] <= 0), "non-positive stay duration")
    _drop(
        df["stay_minutes"].notna() & (df["stay_minutes"] < spec.min_stay_minutes),
        f"stay shorter than {spec.min_stay_minutes} min (no landmark possible)",
    )
    if spec.max_stay_hours is not None:
        _drop(
            df["stay_minutes"].notna() & (df["stay_minutes"] > spec.max_stay_hours * 60),
            f"stay longer than {spec.max_stay_hours:g} h (administrative artifact)",
        )

    # -- observations ---------------------------------------------------------
    if spec.require_any_observation:
        seen = pd.Index(pd.unique(vitals["stay_id"]))
        _drop(~df["stay_id"].isin(seen), "no observations recorded")

    df["is_censored"] = df["disposition"].isin(CENSORING_DISPOSITIONS)
    rep.n_output = len(df)
    return df.reset_index(drop=True), rep


def characterise(
    cohort: pd.DataFrame,
    vitals: pd.DataFrame | None = None,
    by: str | None = None,
) -> pd.DataFrame:
    """A "Table 1" -- what the cohort is made of.

    Parameters
    ----------
    by : optional column to stratify on (e.g. ``"disposition"``).

    Notes
    -----
    Continuous variables are reported as median [IQR] rather than mean (SD):
    ED length of stay is strongly right-skewed, and a mean would describe a
    patient who does not exist.
    """
    def _one(df: pd.DataFrame) -> pd.Series:
        out: dict[str, object] = {
            "stays": len(df),
            "patients": df["patient_id"].nunique(),
            "stays per patient": round(len(df) / max(df["patient_id"].nunique(), 1), 2),
        }
        if "stay_minutes" in df:
            q = df["stay_minutes"].quantile([0.25, 0.5, 0.75]) / 60.0
            out["stay hours, median [IQR]"] = (
                f"{q[0.5]:.1f} [{q[0.25]:.1f}-{q[0.75]:.1f}]"
            )
        for col in ("gender", "arrival_transport", "disposition"):
            if col in df.columns:
                vc = df[col].value_counts(normalize=True)
                if len(vc):
                    out[f"{col}, most common"] = f"{vc.index[0]} ({100 * vc.iloc[0]:.0f}%)"
        if "is_censored" in df:
            out["censored (LWBS/eloped/AMA)"] = (
                f"{int(df['is_censored'].sum())} ({100 * df['is_censored'].mean():.1f}%)"
            )
        if vitals is not None:
            n = vitals[vitals["stay_id"].isin(df["stay_id"])].groupby("stay_id").size()
            n = n.reindex(df["stay_id"], fill_value=0)
            out["observations per stay, median [IQR]"] = (
                f"{n.median():.0f} [{n.quantile(0.25):.0f}-{n.quantile(0.75):.0f}]"
            )
        return pd.Series(out)

    if by is None:
        return _one(cohort).to_frame("all")
    cols = {"all": _one(cohort)}
    for value, grp in cohort.groupby(by, dropna=False):
        cols[str(value)] = _one(grp)
    return pd.DataFrame(cols)


def missingness_by_channel(cohort: pd.DataFrame, vitals: pd.DataFrame) -> pd.DataFrame:
    """Per-channel missingness -- a feature, not a nuisance (PLAN.md 5.1).

    Reports both the share of *observations* lacking a channel and the share of
    *stays* where the channel was never recorded at all. Those are different
    questions: a channel measured once and then never again is present at the
    stay level and absent for most of the trajectory.
    """
    from vigil.data.schema import VITAL_COLS

    v = vitals[vitals["stay_id"].isin(cohort["stay_id"])]
    rows = []
    for c in VITAL_COLS:
        if c not in v.columns:
            continue
        ever = v.groupby("stay_id")[c].apply(lambda s: s.notna().any())
        ever = ever.reindex(cohort["stay_id"], fill_value=False)
        rows.append({
            "channel": c,
            "pct_observations_missing": round(100.0 * v[c].isna().mean(), 1),
            "pct_stays_never_recorded": round(100.0 * (~ever).mean(), 1),
        })
    return pd.DataFrame(rows).sort_values("pct_observations_missing", ascending=False,
                                          ignore_index=True)
