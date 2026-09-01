"""Age-banded physiological reference ranges.

The Round 2 brief is explicit that this matters:

    "Vital sign thresholds and symptom weights differ significantly across
    pediatric, adult, and geriatric populations - a fever of 38.5 C carries
    different clinical urgency in a 3-year-old versus a 75-year-old. Solutions
    that apply a single adult-calibrated scoring model across all age groups
    introduce silent safety risk."

A heart rate of 150 is an emergency in an adult and unremarkable in an infant.
A single adult-calibrated model does not merely lose accuracy on children - it
is confidently wrong in the direction that kills, because a compensating child
looks *tachycardic but normotensive*, which an adult model reads as stable.

So the age band is resolved first, and every downstream threshold is a function
of it. There is no adult default: an unknown age is a first-class state that
lowers confidence rather than silently assuming 40 years old.

Reference ranges follow published PALS/APLS paediatric vital-sign tables. They
are **illustrative defaults, site-configurable by design** - a real deployment
recalibrates them against its own case mix (PLAN.md; the brief's scalability
requirement).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AgeBand(str, Enum):
    """Physiologically distinct bands, not administrative ones."""

    NEONATE = "neonate"          # < 1 month
    INFANT = "infant"            # 1-12 months
    TODDLER = "toddler"          # 1-3 years
    PRESCHOOL = "preschool"      # 3-5 years
    SCHOOL_AGE = "school_age"    # 5-12 years
    ADOLESCENT = "adolescent"    # 12-18 years
    ADULT = "adult"              # 18-65 years
    GERIATRIC = "geriatric"      # 65+ years
    UNKNOWN = "unknown"          # age not recorded - never silently an adult

    @property
    def is_paediatric(self) -> bool:
        return self in _PAEDIATRIC

    @property
    def label(self) -> str:
        return self.value.replace("_", " ")


_PAEDIATRIC = frozenset({
    AgeBand.NEONATE, AgeBand.INFANT, AgeBand.TODDLER,
    AgeBand.PRESCHOOL, AgeBand.SCHOOL_AGE, AgeBand.ADOLESCENT,
})


@dataclass(frozen=True)
class VitalRanges:
    """Normal awake ranges for one age band.

    ``hypotension_sbp`` is the threshold below which systolic pressure is
    frank hypotension for this band. In children it is deliberately *low*:
    paediatric blood pressure is maintained by vasoconstriction until
    decompensation, so a normal SBP in a tachycardic child is not reassurance
    (see :mod:`vigil.clinical.redflags`, ``compensated_shock``).
    """

    hr: tuple[int, int]
    rr: tuple[int, int]
    sbp: tuple[int, int]
    hypotension_sbp: int
    fever_temp_c: float = 38.0


# PALS/APLS reference ranges. Illustrative defaults; site-configurable.
RANGES: dict[AgeBand, VitalRanges] = {
    AgeBand.NEONATE:    VitalRanges(hr=(100, 205), rr=(30, 60), sbp=(60, 84), hypotension_sbp=60, fever_temp_c=38.0),
    AgeBand.INFANT:     VitalRanges(hr=(100, 180), rr=(30, 53), sbp=(70, 104), hypotension_sbp=70, fever_temp_c=38.0),
    AgeBand.TODDLER:    VitalRanges(hr=(98, 140), rr=(22, 37), sbp=(72, 110), hypotension_sbp=72, fever_temp_c=38.0),
    AgeBand.PRESCHOOL:  VitalRanges(hr=(80, 120), rr=(20, 28), sbp=(74, 112), hypotension_sbp=74, fever_temp_c=38.0),
    AgeBand.SCHOOL_AGE: VitalRanges(hr=(75, 118), rr=(18, 25), sbp=(80, 120), hypotension_sbp=80, fever_temp_c=38.0),
    AgeBand.ADOLESCENT: VitalRanges(hr=(60, 100), rr=(12, 20), sbp=(90, 130), hypotension_sbp=90, fever_temp_c=38.0),
    AgeBand.ADULT:      VitalRanges(hr=(60, 100), rr=(12, 20), sbp=(100, 140), hypotension_sbp=90, fever_temp_c=38.0),
    # Geriatric: a blunted febrile response is why "no fever" excludes nothing.
    # The threshold is lowered, and hypothermia is separately a red flag.
    AgeBand.GERIATRIC:  VitalRanges(hr=(60, 100), rr=(12, 20), sbp=(110, 150), hypotension_sbp=100, fever_temp_c=37.5),
}


def band_for_age(age_years: float | None) -> AgeBand:
    """Resolve an age in years to its band.

    ``None`` maps to :attr:`AgeBand.UNKNOWN`, never to adult. An unknown age is
    a known unknown: it must reduce confidence (:mod:`vigil.triage.confidence`)
    rather than quietly borrow adult thresholds.
    """
    if age_years is None:
        return AgeBand.UNKNOWN
    if age_years < 0:
        raise ValueError(f"age_years must be non-negative, got {age_years}")
    if age_years < 1 / 12:
        return AgeBand.NEONATE
    if age_years < 1:
        return AgeBand.INFANT
    if age_years < 3:
        return AgeBand.TODDLER
    if age_years < 5:
        return AgeBand.PRESCHOOL
    if age_years < 12:
        return AgeBand.SCHOOL_AGE
    if age_years < 18:
        return AgeBand.ADOLESCENT
    if age_years < 65:
        return AgeBand.ADULT
    return AgeBand.GERIATRIC


def ranges_for(band: AgeBand) -> VitalRanges:
    """Reference ranges for a band.

    UNKNOWN falls back to adult ranges *for arithmetic only* - callers must
    already have degraded confidence. Returning adult numbers silently is the
    exact failure this module exists to prevent, so the fallback is documented
    here and its consequence is enforced in the confidence layer.
    """
    return RANGES.get(band, RANGES[AgeBand.ADULT])


def hypotension_sbp_for(band: AgeBand, age_years: float | None = None) -> float:
    """Systolic threshold for frank hypotension.

    For children aged 1-10 the PALS formula ``70 + 2 x age`` is finer-grained
    than a band constant, so it is preferred when the exact age is known.
    """
    if age_years is not None and 1 <= age_years < 10:
        return 70.0 + 2.0 * age_years
    return float(ranges_for(band).hypotension_sbp)


def vital_status(band: AgeBand, vital: str, value: float | None) -> str:
    """Classify one vital against its band: ``low`` / ``normal`` / ``high``.

    Returns ``"unknown"`` when the value is absent - absence is a state, not a
    zero.
    """
    if value is None:
        return "unknown"
    rng = ranges_for(band)
    bounds = {"hr": rng.hr, "rr": rng.rr, "sbp": rng.sbp}.get(vital)
    if bounds is None:
        raise KeyError(f"no band range for vital {vital!r}")
    lo, hi = bounds
    if value < lo:
        return "low"
    if value > hi:
        return "high"
    return "normal"
