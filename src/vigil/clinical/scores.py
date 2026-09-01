"""Early-warning scores, selected by age band.

Two instruments, because one does not fit:

* :func:`news2` - the Royal College of Physicians' National Early Warning Score
  2, for patients 16 and over. Faithful to the published aggregate table.
* :func:`pews` - a PEWS-style paediatric score computed over the age-banded
  reference ranges in :mod:`vigil.clinical.agebands`. **Explicitly an
  illustrative implementation**, not a claim to reproduce any one validated
  instrument: published PEWS variants differ by site, and asserting fidelity we
  have not checked would be worse than saying so.

:func:`monitoring_interval` is the other half of the brief's WATCH mandate:

    "The system must monitor patients already in the waiting queue and trigger
    re-assessment if wait time exceeds safe thresholds for their severity level"

NEWS2 already ties monitoring frequency to score band. We are not inventing a
new care model - we are executing an existing one that collapses under crowding.

**Every score returns its own component breakdown.** A number a clinician cannot
take apart in the ten seconds they have is not explainable, and the brief
requires decisions to be "explainable within seconds".
"""
from __future__ import annotations

from dataclasses import dataclass, field

from vigil.clinical.agebands import AgeBand, ranges_for

#: Level of consciousness, ACVPU. Anything below Alert scores in NEWS2.
ACVPU = ("A", "C", "V", "P", "U")


@dataclass(frozen=True)
class ScoreResult:
    """An early-warning score with its full derivation.

    Attributes
    ----------
    total : aggregate score.
    components : per-parameter contribution, for display and audit.
    missing : parameters that could not be scored. **Never treated as zero** -
        a missing respiratory rate is not a normal respiratory rate, and the
        count propagates into the confidence layer.
    max_single : highest single-parameter score; in NEWS2 a single 3 triggers
        escalation on its own regardless of the total.
    instrument : which score was used, so the band choice is auditable.
    """

    total: int
    components: dict[str, int] = field(default_factory=dict)
    missing: tuple[str, ...] = ()
    max_single: int = 0
    instrument: str = "news2"

    @property
    def has_red_parameter(self) -> bool:
        """A single parameter scoring 3 - urgent regardless of the total."""
        return self.max_single >= 3


def _score_bands(value: float | None, bands: list[tuple[float, float, int]]) -> int | None:
    """First matching ``(low, high, points)`` band, inclusive. ``None`` if absent."""
    if value is None:
        return None
    for lo, hi, pts in bands:
        if lo <= value <= hi:
            return pts
    return None


# ── NEWS2 (RCP, adults 16+) ───────────────────────────────────────────────────
_INF = float("inf")

_NEWS2_RR = [(-_INF, 8, 3), (9, 11, 1), (12, 20, 0), (21, 24, 2), (25, _INF, 3)]
_NEWS2_SPO2 = [(-_INF, 91, 3), (92, 93, 2), (94, 95, 1), (96, _INF, 0)]
_NEWS2_SBP = [(-_INF, 90, 3), (91, 100, 2), (101, 110, 1), (111, 219, 0), (220, _INF, 3)]
_NEWS2_HR = [(-_INF, 40, 3), (41, 50, 1), (51, 90, 0), (91, 110, 1), (111, 130, 2), (131, _INF, 3)]
_NEWS2_TEMP = [(-_INF, 35.0, 3), (35.1, 36.0, 1), (36.1, 38.0, 0), (38.1, 39.0, 1), (39.1, _INF, 2)]


def news2(
    *,
    rr: float | None = None,
    spo2: float | None = None,
    on_oxygen: bool | None = None,
    sbp: float | None = None,
    hr: float | None = None,
    temp_c: float | None = None,
    consciousness: str | None = None,
) -> ScoreResult:
    """National Early Warning Score 2, for patients 16 and over.

    Parameters
    ----------
    consciousness : ACVPU letter. Anything other than ``"A"`` scores 3 - new
        confusion is a NEWS2 red parameter, which is precisely the presentation
        that gets written off as "just confused" in an elderly patient.
    on_oxygen : supplemental oxygen scores 2. ``None`` is treated as unknown
        and reported as missing rather than assumed to be room air.
    """
    parts: dict[str, int | None] = {
        "respiratory_rate": _score_bands(rr, _NEWS2_RR),
        "spo2": _score_bands(spo2, _NEWS2_SPO2),
        "supplemental_oxygen": None if on_oxygen is None else (2 if on_oxygen else 0),
        "systolic_bp": _score_bands(sbp, _NEWS2_SBP),
        "pulse": _score_bands(hr, _NEWS2_HR),
        "temperature": _score_bands(temp_c, _NEWS2_TEMP),
        "consciousness": None if consciousness is None else (0 if consciousness.upper() == "A" else 3),
    }
    scored = {k: v for k, v in parts.items() if v is not None}
    missing = tuple(k for k, v in parts.items() if v is None)
    return ScoreResult(
        total=sum(scored.values()),
        components=scored,
        missing=missing,
        max_single=max(scored.values(), default=0),
        instrument="NEWS2",
    )


# ── PEWS-style paediatric score ───────────────────────────────────────────────

def pews(
    band: AgeBand,
    *,
    hr: float | None = None,
    rr: float | None = None,
    spo2: float | None = None,
    sbp: float | None = None,
    temp_c: float | None = None,
    consciousness: str | None = None,
    work_of_breathing: str | None = None,
    age_years: float | None = None,
) -> ScoreResult:
    """A PEWS-style score over age-banded reference ranges.

    Illustrative implementation (see module docstring). Each parameter scores
    0-3 by how far outside its band range it sits.

    The clinically important asymmetry: **tachycardia scores, and a normal
    blood pressure does not subtract.** Children compensate by raising heart
    rate and vasoconstricting; blood pressure falls last, so a normotensive
    tachycardic child may be in compensated shock. Nothing in this function
    lets a reassuring SBP cancel a worrying HR.
    """
    from vigil.clinical.agebands import hypotension_sbp_for

    rng = ranges_for(band)
    parts: dict[str, int | None] = {}

    def _deviation(value: float | None, lo: float, hi: float, span: float) -> int | None:
        """0 inside range; 1/2/3 by how many `span`-wide steps outside it."""
        if value is None:
            return None
        if lo <= value <= hi:
            return 0
        excess = (value - hi) if value > hi else (lo - value)
        return int(min(3, 1 + excess // span))

    parts["heart_rate"] = _deviation(hr, *rng.hr, span=max(10.0, (rng.hr[1] - rng.hr[0]) / 4))
    parts["respiratory_rate"] = _deviation(rr, *rng.rr, span=max(4.0, (rng.rr[1] - rng.rr[0]) / 4))

    if spo2 is None:
        parts["spo2"] = None
    else:
        parts["spo2"] = 3 if spo2 < 90 else 2 if spo2 < 94 else 1 if spo2 < 96 else 0

    if sbp is None:
        parts["systolic_bp"] = None
    else:
        floor = hypotension_sbp_for(band, age_years)
        # Hypotension in a child is late and ominous - it goes straight to 3.
        parts["systolic_bp"] = 3 if sbp < floor else 0

    if temp_c is None:
        parts["temperature"] = None
    else:
        parts["temperature"] = 2 if (temp_c < 36.0 or temp_c >= 39.0) else (
            1 if temp_c >= rng.fever_temp_c else 0
        )

    parts["consciousness"] = None if consciousness is None else (
        0 if consciousness.upper() == "A" else 3
    )
    if work_of_breathing is not None:
        parts["work_of_breathing"] = {"normal": 0, "mild": 1, "moderate": 2, "severe": 3}.get(
            work_of_breathing.lower(), 0
        )

    scored = {k: v for k, v in parts.items() if v is not None}
    missing = tuple(k for k, v in parts.items() if v is None)
    return ScoreResult(
        total=sum(scored.values()),
        components=scored,
        missing=missing,
        max_single=max(scored.values(), default=0),
        instrument=f"PEWS-style ({band.label})",
    )


def early_warning_score(band: AgeBand, **kw) -> ScoreResult:
    """Dispatch to the instrument appropriate for the age band.

    This one line is the brief's paediatric requirement: the instrument is
    chosen by physiology, never defaulted to the adult one.
    """
    if band.is_paediatric:
        allowed = {"hr", "rr", "spo2", "sbp", "temp_c", "consciousness",
                   "work_of_breathing", "age_years"}
        return pews(band, **{k: v for k, v in kw.items() if k in allowed})
    allowed = {"rr", "spo2", "on_oxygen", "sbp", "hr", "temp_c", "consciousness"}
    return news2(**{k: v for k, v in kw.items() if k in allowed})


# ── the re-assessment clock ───────────────────────────────────────────────────

@dataclass(frozen=True)
class MonitoringPlan:
    """How often this patient must be re-checked, and why."""

    interval_minutes: int
    rationale: str
    band: str


def monitoring_interval(score: ScoreResult, band: AgeBand) -> MonitoringPlan:
    """Re-assessment interval implied by the score.

    Adult intervals follow the NEWS2 clinical-response table. Paediatric
    intervals are tightened: children decompensate faster and with less
    warning, so the same nominal risk earns a shorter clock.

    This function is the WATCH loop's backbone. It does not create work - it
    orders work the department already owes.
    """
    if band.is_paediatric:
        if score.total >= 7 or score.max_single >= 3:
            return MonitoringPlan(15, "high paediatric score or a single red parameter", "high")
        if score.total >= 5:
            return MonitoringPlan(30, "medium paediatric score", "medium")
        if score.total >= 3:
            return MonitoringPlan(60, "low-medium paediatric score", "low-medium")
        return MonitoringPlan(120, "low paediatric score", "low")

    if score.total >= 7:
        return MonitoringPlan(15, "NEWS2 >= 7: continuous monitoring band", "high")
    if score.total >= 5:
        return MonitoringPlan(30, "NEWS2 5-6: minimum hourly, tightened for a waiting patient", "medium")
    if score.has_red_parameter:
        return MonitoringPlan(60, "single NEWS2 parameter scoring 3", "single-red")
    if score.total >= 1:
        return MonitoringPlan(240, "NEWS2 1-4: minimum 4-6 hourly", "low")
    return MonitoringPlan(360, "NEWS2 0: minimum 12 hourly, tightened for an undifferentiated ED patient", "low")
