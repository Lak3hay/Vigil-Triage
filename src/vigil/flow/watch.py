"""Trends, not thresholds - the deterioration detector.

The failure this exists to catch:

    A patient is triaged at minute 1, correctly, and marked stable. Over the
    next ninety minutes their heart rate climbs 88 -> 99 and their oxygen
    saturation drifts 99 -> 95. **Every single reading is inside the normal
    range.** No threshold fires. Nobody looks again. At minute 90 they are in
    septic shock.

A point-in-time score cannot see that, because at no point in time is anything
abnormal. The signal is in the *direction of travel*, and it is only visible if
someone compares two readings - which is exactly what stops happening when a
department is full.

Two readings are enough. This does not require continuous telemetry, waveform
capture, or a monitor per chair: HR 88 -> 99 across two checks forty minutes
apart *is* the signal. That matters because it makes the whole approach
deployable on a shared vitals kiosk and a re-check clock rather than on
hardware nobody will buy.

Detection is deterministic and explainable by construction - a slope and a
delta, both of which a clinician can verify from the same two numbers they
already have. A learned model over these trajectories is a separate, optional
layer (``vigil.models``); it is not permitted to *suppress* anything this
module raises, only to raise more.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from vigil.clinical.scores import ScoreResult

#: Channels where a *rise* is the worrying direction.
_RISING_IS_BAD = ("hr", "rr")
#: Channels where a *fall* is the worrying direction.
_FALLING_IS_BAD = ("spo2", "sbp")

#: Minimum change over the window before a slope counts as real rather than
#: measurement noise. Deliberately above typical inter-observer variation:
#: a manual pulse count varies by a few beats between nurses, so a 1-2 bpm
#: "trend" is an artefact of who held the wrist.
NOISE_FLOOR = {"hr": 8.0, "rr": 3.0, "spo2": 2.0, "sbp": 10.0}


@dataclass(frozen=True)
class Observation:
    """One set of vitals at one time, plus the score computed from them."""

    at: datetime
    hr: float | None = None
    rr: float | None = None
    spo2: float | None = None
    sbp: float | None = None
    temp_c: float | None = None
    score_total: int | None = None

    def get(self, channel: str) -> float | None:
        return getattr(self, channel, None)


@dataclass(frozen=True)
class TrendSignal:
    """Whether this patient is getting worse, and the evidence for it."""

    worsening: bool
    reasons: tuple[str, ...] = ()
    deltas: dict[str, float] = field(default_factory=dict)
    window_minutes: float = 0.0
    n_observations: int = 0

    #: True when the trend is worsening while every individual reading is still
    #: inside its normal range - the case a threshold system cannot see, and the
    #: entire reason this module exists.
    silent: bool = False

    @property
    def headline(self) -> str:
        if not self.worsening:
            return "stable"
        return "deteriorating (all readings still individually normal)" if self.silent else "deteriorating"


def _delta(observations: list[Observation], channel: str) -> float | None:
    """Net change in a channel across the window, first to last recorded."""
    vals = [(o.at, o.get(channel)) for o in observations if o.get(channel) is not None]
    if len(vals) < 2:
        return None
    return vals[-1][1] - vals[0][1]


def detect_trend(
    observations: list[Observation],
    *,
    in_normal_range: dict[str, bool] | None = None,
) -> TrendSignal:
    """Compare the earliest and latest observations in the window.

    Parameters
    ----------
    observations
        Chronologically ordered. Two are sufficient; more sharpen the estimate.
    in_normal_range
        Per-channel flag for whether the *latest* value sits inside its
        age-banded normal range. Used only to mark a trend as ``silent``, which
        is what makes the finding worth showing to a clinician who has already
        looked at the numbers and seen nothing wrong.
    """
    obs = sorted(observations, key=lambda o: o.at)
    if len(obs) < 2:
        return TrendSignal(worsening=False, n_observations=len(obs))

    window = (obs[-1].at - obs[0].at).total_seconds() / 60.0
    reasons: list[str] = []
    deltas: dict[str, float] = {}

    for channel in _RISING_IS_BAD + _FALLING_IS_BAD:
        d = _delta(obs, channel)
        if d is None:
            continue
        deltas[channel] = round(d, 1)
        floor = NOISE_FLOOR[channel]
        worsened = d >= floor if channel in _RISING_IS_BAD else d <= -floor
        if worsened:
            direction = "rising" if channel in _RISING_IS_BAD else "falling"
            reasons.append(f"{channel.upper()} {direction} by {abs(d):.0f} over {window:.0f} min")

    # The classic occult-deterioration pattern: the body is working harder to
    # hold oxygenation. Either signal alone is weak; together they are not.
    hr_up = deltas.get("hr", 0) >= NOISE_FLOOR["hr"]
    spo2_down = deltas.get("spo2", 0) <= -NOISE_FLOOR["spo2"]
    if hr_up and spo2_down:
        reasons.append("rising heart rate with falling oxygen saturation - classic occult deterioration")

    # The score itself moving is the most interpretable signal of all, because
    # it is the number the department already acts on.
    scores = [o.score_total for o in obs if o.score_total is not None]
    if len(scores) >= 2 and scores[-1] - scores[0] >= 2:
        reasons.append(f"early-warning score rose {scores[0]} -> {scores[-1]}")

    worsening = bool(reasons)
    silent = False
    if worsening and in_normal_range:
        moved = [c for c in deltas if c in in_normal_range]
        silent = bool(moved) and all(in_normal_range.get(c, False) for c in moved)

    return TrendSignal(
        worsening=worsening,
        reasons=tuple(reasons),
        deltas=deltas,
        window_minutes=round(window, 1),
        n_observations=len(obs),
        silent=silent,
    )


def normal_range_flags(band, latest: Observation) -> dict[str, bool]:
    """Which of the latest readings are inside their age-banded normal range.

    Age-banded, because "normal" is not a constant: a heart rate of 130 is
    outside range for an adult and inside it for an infant, and asking the
    question against adult ranges is how a deteriorating child gets called
    stable.
    """
    from vigil.clinical.agebands import ranges_for, vital_status

    flags: dict[str, bool] = {}
    for channel in ("hr", "rr", "sbp"):
        v = latest.get(channel)
        if v is not None:
            flags[channel] = vital_status(band, channel, v) == "normal"
    if latest.spo2 is not None:
        flags["spo2"] = latest.spo2 >= 95
    return flags


def score_delta(before: ScoreResult, after: ScoreResult) -> int:
    """Change in early-warning score. Positive means worse."""
    return after.total - before.total
