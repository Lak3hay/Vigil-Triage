"""NHAMCS loader - the Plan B source (public, no credentialing).

NOT YET IMPLEMENTED, and deliberately so: it exists to hold the seam open and to
record what it can and cannot support, so the decision is visible in code rather
than in someone's memory.

NHAMCS records ONE observation set per visit. It therefore supports the
disagreement analysis, alert-budget evaluation, calibration and the subgroup
audit - but it CANNOT support the trajectory model, which is the project's
headline. Nothing in the landmark pipeline should silently appear to work on it.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from vigil.data.base import register


@register("nhamcs")
class NHAMCS:
    SUPPORTS_TRAJECTORY = False

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def stays(self) -> pd.DataFrame:
        raise NotImplementedError("NHAMCS loader not written yet - see PLAN.md 9.1")

    def triage(self) -> pd.DataFrame:
        raise NotImplementedError("NHAMCS loader not written yet - see PLAN.md 9.1")

    def vitals(self) -> pd.DataFrame:
        raise NotImplementedError(
            "NHAMCS has one observation set per visit and cannot support the "
            "trajectory model. Use it for the cross-sectional analyses only."
        )
