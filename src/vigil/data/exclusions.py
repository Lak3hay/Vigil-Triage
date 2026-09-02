"""Exclusion accounting.

Dropping rows is a measurement decision, not housekeeping. A filter that removes
4% of survivors and 22% of deaths has stopped being a filter and become the effect
being measured (PLAN.md 3.2) -- and it will not look any different in the totals.

So every filter records *which* units it removed, not just how many, which is what
makes the per-outcome breakdown in :meth:`ExclusionReport.stratify` possible after
the fact. Labels usually arrive later than the cohort does; keeping the identities
means the audit does not have to be planned for in advance.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


class UnitMismatchError(ValueError):
    """Raised when a stratified audit would divide counts of one unit by another."""


@dataclass
class ExclusionReport:
    """Firing rate of every filter in one pipeline stage.

    Parameters
    ----------
    stage : name of the stage, used in the printed header.
    unit  : what is being counted -- "stay", "landmark", "observation".
    """

    stage: str
    unit: str = "row"
    n_input: int = 0
    n_output: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    removed: dict[str, np.ndarray] = field(default_factory=dict)

    # ── recording ────────────────────────────────────────────────────────────
    def drop(self, ids, reason: str) -> None:
        """Record units removed for `reason`, keeping their identities."""
        arr = np.asarray(ids)
        if reason in self.removed:
            arr = np.concatenate([self.removed[reason], arr])
        self.removed[reason] = arr
        self.reasons[reason] = len(arr)

    def record(self, reason: str, n: int) -> None:
        """Record a count without identities (when ids are not addressable)."""
        self.reasons[reason] = self.reasons.get(reason, 0) + int(n)

    # ── reporting ────────────────────────────────────────────────────────────
    def to_frame(self) -> pd.DataFrame:
        rows = [
            {
                "reason": r,
                "n": n,
                "pct_of_input": round(100.0 * n / self.n_input, 2) if self.n_input else 0.0,
            }
            for r, n in self.reasons.items()
        ]
        return pd.DataFrame(rows, columns=["reason", "n", "pct_of_input"])

    def stratify(
        self, outcome: pd.Series, denominator: pd.Series | None = None
    ) -> pd.DataFrame:
        """Removal rate of every filter, per outcome class.

        Parameters
        ----------
        outcome
            Indexed by the same ids passed to :meth:`drop`, holding the class of
            each unit *before* any exclusion.
        denominator
            Class -> number of eligible units, when that differs from the number
            of rows in ``outcome``. Required when a stage drops units at a finer
            granularity than the outcome is indexed at -- e.g. landmark drops
            keyed by ``stay_id``, where one stay contributes several landmarks.
            Without it the numerator would count landmarks and the denominator
            stays, and the resulting percentage would be meaningless.

        Returns
        -------
        DataFrame
            One row per reason, with ``n`` and ``pct`` per class and a ``disparity``
            column: the widest gap in removal rate between any two classes. **That
            column is the one to read** -- a filter with a large disparity is
            selecting on the outcome.
        """
        if not self.removed:
            return pd.DataFrame(columns=["reason", "disparity"])

        ids_all = np.concatenate(list(self.removed.values()))
        if denominator is None and len(ids_all) != len(np.unique(ids_all)):
            raise UnitMismatchError(
                "ids passed to drop() repeat, so this stage removes units finer "
                "than `outcome` is indexed at (e.g. landmarks keyed by stay_id). "
                "Counting them against per-stay totals mixes units and yields a "
                "meaningless percentage. Pass `denominator=` with the number of "
                "eligible units per class."
            )

        classes = sorted(pd.unique(outcome.dropna()))
        denom = denominator if denominator is not None else outcome.value_counts()
        rows = []
        for reason, ids in self.removed.items():
            cls = outcome.reindex(pd.Index(ids)).dropna()
            rec: dict[str, object] = {"reason": reason}
            pcts = []
            for c in classes:
                n = int((cls == c).sum())
                pct = 100.0 * n / denom[c] if denom.get(c) else 0.0
                rec[f"n[{c}]"] = n
                rec[f"pct[{c}]"] = round(pct, 2)
                pcts.append(pct)
            rec["disparity"] = round(max(pcts) - min(pcts), 2) if pcts else 0.0
            rows.append(rec)
        return pd.DataFrame(rows).sort_values("disparity", ascending=False, ignore_index=True)

    def worst_disparity(
        self, outcome: pd.Series, denominator: pd.Series | None = None
    ) -> tuple[str, float] | None:
        """The single filter most likely to be biasing, and by how much."""
        s = self.stratify(outcome, denominator)
        if s.empty:
            return None
        top = s.iloc[0]
        return str(top["reason"]), float(top["disparity"])

    def __str__(self) -> str:
        head = f"[{self.stage}] {self.n_input} -> {self.n_output} {self.unit}s"
        if not self.reasons:
            return head + "  (nothing dropped)"
        width = max(len(r) for r in self.reasons)
        body = "\n".join(
            f"    {r:<{width}s} {n:>8d}  ({100.0 * n / self.n_input:5.1f}%)"
            for r, n in self.reasons.items()
        )
        kept = self.n_output
        pct = 100.0 * kept / self.n_input if self.n_input else 0.0
        return f"{head}\n{body}\n    {'RETAINED':<{width}s} {kept:>8d}  ({pct:5.1f}%)"
