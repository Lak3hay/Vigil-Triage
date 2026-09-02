"""Clinical logic: age bands, early-warning scores, and the red-flag panel.

Deterministic and offline by design. Every number here is reproducible from the
inputs alone -- no model server, no language model, no network.
"""
from vigil.clinical.agebands import AgeBand, band_for_age, ranges_for
from vigil.clinical.redflags import PANEL, RedFlag, evaluate
from vigil.clinical.scores import ScoreResult, early_warning_score, monitoring_interval, news2, pews

__all__ = [
           "PANEL",
           "AgeBand",
           "RedFlag",
           "ScoreResult",
           "band_for_age",
           "early_warning_score",
           "evaluate",
           "monitoring_interval",
           "news2",
           "pews",
           "ranges_for",
]
