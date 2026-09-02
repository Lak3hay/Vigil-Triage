"""The second axis: what does waiting cost this patient?

Acuity asks *how sick are you*. It is a categorical snapshot, assigned once and
in practice never revisited. But the decision an emergency department actually
makes is a **sequencing decision under scarcity**, and that is a different
question: a stable fracture is not very sick yet waiting genuinely harms them;
a resolved seizure is sick but waiting is comparatively safe.

So Vigil adds a second, continuous axis alongside acuity - a **cost-of-waiting
curve** per patient, which rises with every minute they are not seen.

Four things fall out of this one equation rather than being bolted on:

* **Sequencing within a band** - steepest curve goes first.
* **Waiting-room re-ranking** - the curve *steepens* when someone deteriorates,
  so a patient who is getting worse rises past patients who are merely older in
  the queue.
* **Anti-starvation** - because the curve is convex in waiting time, a level-4
  patient who has waited four hours eventually overtakes a level-3 patient who
  has waited thirty minutes. The algorithm *cannot* starve anyone forever. This
  is a mathematical property, not a fairness patch.

  **With one deliberate exception: level 1 is absolute.** A level-1 patient
  needs an immediate life-saving intervention, so their target is zero minutes
  and their cost is unbounded from the first second. Nothing overtakes them,
  however long anyone else has waited. Anti-starvation is a guarantee among the
  patients who actually wait - levels 2 to 5 - and making it universal would
  mean letting a queue of minor complaints eventually outrank a cardiac arrest,
  which is not fairness.
* **Routing** - the same numbers choose the stream.

## This is a declared policy, not an estimated quantity

Stated plainly because it is the honest boundary of the claim:

    We do **not** estimate "how much worse will your outcome be if you wait 30
    more minutes" from data. That is a causal quantity, and it is confounded in
    the obvious direction - sicker patients are seen sooner, so naive
    estimation concludes that waiting is *good* for you. Recovering it honestly
    needs an instrument or a natural experiment, and we have neither.

What this module is instead: an explicit, readable, **auditable scheduling
policy**. Every constant below is visible, justified, and site-configurable.
That is a strength rather than a compromise - a hospital's clinical governance
lead can read this file and change it, which is impossible with a learned
ranking model, and it is how the same engine flexes across a 120-visit rural
department and a 500-visit urban one without retraining anything.

Time-to-be-seen targets are the ones triage systems already publish (ATS/CTAS
style). We are not inventing new care standards - we are scheduling against the
ones that already exist and that crowding causes departments to miss.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Acuity 1 (immediate) - 5 (non-urgent). Lower is more urgent.
LEVELS = (1, 2, 3, 4, 5)

#: Level 1 is a separate priority class, not a point on the curve. Adding this
#: floor to their cost puts them above anything any other level can reach at any
#: waiting time, while still letting level-1 patients order among *themselves*
#: by how long they have waited. Implemented as a constant rather than infinity
#: so the number still displays and still sorts.
IMMEDIATE_PRIORITY_FLOOR = 1e6


@dataclass(frozen=True)
class HarmPolicy:
    """Declared cost-of-waiting policy for one site.

    Attributes
    ----------
    target_minutes
        Time-to-be-seen target per acuity level. Published triage standards,
        not invented ones.
    rate_at_target
        Harm units accrued at exactly the target time. Sets the *relative*
        weight of the levels: a level-1 patient at target is worth far more
        attention than a level-5 patient at target.
    convexity
        Exponent on normalised waiting time. **Must exceed 1**, and that is the
        anti-starvation guarantee: with ``gamma > 1`` every patient's curve
        steepens the longer they wait, so any patient overtakes any higher-band
        patient given enough time. At ``gamma = 1`` the ordering would be fixed
        by band forever and low-acuity patients could starve.
    deterioration_multiplier
        Applied when the WATCH loop reports a worsening trend. This is what
        makes a deteriorating patient climb the board.
    breach_multiplier
        Applied once a patient is past their target. Makes a breach visible as
        a scheduling fact rather than a report written later.
    unresolved_flag_multiplier
        Applied when a red flag is raised and no clinician has yet accepted or
        dismissed it. Uncertainty that nobody has looked at is itself urgent.
    """

    name: str = "default"
    target_minutes: dict[int, float] = field(
        default_factory=lambda: {1: 0.0, 2: 10.0, 3: 60.0, 4: 120.0, 5: 240.0}
    )
    rate_at_target: dict[int, float] = field(
        default_factory=lambda: {1: 100.0, 2: 40.0, 3: 12.0, 4: 4.0, 5: 1.5}
    )
    convexity: float = 1.5
    deterioration_multiplier: float = 3.0
    breach_multiplier: float = 1.6
    unresolved_flag_multiplier: float = 1.4

    def __post_init__(self) -> None:
        if self.convexity <= 1.0:
            raise ValueError(
                "convexity must exceed 1: it is the anti-starvation guarantee. "
                "At gamma <= 1 a low-acuity patient can never overtake a "
                "higher-band one, however long they wait."
            )
        missing = [lv for lv in LEVELS if lv not in self.target_minutes or lv not in self.rate_at_target]
        if missing:
            raise ValueError(f"policy is missing levels {missing}")

    # ── the curve ─────────────────────────────────────────────────────────────
    def cost_of_waiting(
        self,
        level: int,
        waited_minutes: float,
        *,
        deteriorating: bool = False,
        unresolved_flag: bool = False,
    ) -> float:
        """Harm accrued by making *this* patient wait *this* long.

        Not a probability and not a clinical prediction - a scheduling
        quantity, comparable only against other patients under the same policy.
        """
        level = max(1, min(5, int(level)))
        target = max(self.target_minutes[level], 1.0)
        rate = self.rate_at_target[level]

        cost = rate * (max(0.0, waited_minutes) / target) ** self.convexity
        if level == 1:
            # Absolute priority. Nothing overtakes a patient who needs an
            # immediate life-saving intervention, however long anyone else has
            # waited -- that would not be fairness.
            cost += IMMEDIATE_PRIORITY_FLOOR
        if waited_minutes > target:
            cost *= self.breach_multiplier
        if deteriorating:
            cost *= self.deterioration_multiplier
        if unresolved_flag:
            cost *= self.unresolved_flag_multiplier
        return round(cost, 3)

    def minutes_to_breach(self, level: int, waited_minutes: float) -> float:
        """Minutes until this patient passes their time-to-be-seen target.

        Negative once breached. This is the number a charge nurse actually
        acts on, so it is surfaced directly rather than being derivable.
        """
        return round(self.target_minutes[max(1, min(5, int(level)))] - waited_minutes, 1)

    def explain(
        self,
        level: int,
        waited_minutes: float,
        *,
        deteriorating: bool = False,
        unresolved_flag: bool = False,
    ) -> str:
        """One line a clinician can read in two seconds.

        The brief requires decisions to be explainable in seconds by someone
        managing several other patients, so the queue position must justify
        itself without anyone opening a panel.
        """
        target = self.target_minutes[max(1, min(5, int(level)))]
        parts = [f"level {level}, waited {waited_minutes:.0f} of {target:.0f} min"]
        if waited_minutes > target:
            parts.append(f"BREACHED by {waited_minutes - target:.0f} min")
        if deteriorating:
            parts.append("trend worsening")
        if unresolved_flag:
            parts.append("flag not yet reviewed")
        return " · ".join(parts)


# ── site profiles ─────────────────────────────────────────────────────────────
# The brief: "Hospitals differ enormously in scale, specialty mix, and staffing
# - a workflow that works for a large urban trauma center may not transfer to a
# small rural emergency department." The engine does not change between these.
# Only the declared policy does.

URBAN_TRAUMA_CENTRE = HarmPolicy(
    name="urban-trauma-500",
    # High volume, deep resources: standard targets are achievable, so hold them.
    target_minutes={1: 0.0, 2: 10.0, 3: 60.0, 4: 120.0, 5: 240.0},
    rate_at_target={1: 100.0, 2: 40.0, 3: 12.0, 4: 4.0, 5: 1.5},
    convexity=1.5,
)

RURAL_DISTRICT = HarmPolicy(
    name="rural-district-120",
    # Fewer staff and a longer transfer time for anything critical, so the
    # sickest are held to a tighter target (they may need retrieval) while
    # low-acuity targets are relaxed to something the department can actually
    # meet. A target nobody can hit is not a safety standard, it is an alarm
    # generator, and it is how these systems lose staff trust.
    target_minutes={1: 0.0, 2: 8.0, 3: 90.0, 4: 180.0, 5: 300.0},
    rate_at_target={1: 120.0, 2: 50.0, 3: 10.0, 4: 3.0, 5: 1.0},
    convexity=1.6,          # steeper: with fewer staff, starvation risk is higher
    deterioration_multiplier=3.5,
)

PROFILES: dict[str, HarmPolicy] = {
    p.name: p for p in (URBAN_TRAUMA_CENTRE, RURAL_DISTRICT)
}


def profile(name: str) -> HarmPolicy:
    if name not in PROFILES:
        raise KeyError(f"unknown site profile {name!r}; have {sorted(PROFILES)}")
    return PROFILES[name]


# ── routing ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RoutingDecision:
    """Where this patient should go, and why."""

    stream: str
    rationale: str
    is_recommendation: bool = True   # a human accepts it; the system never moves a patient


#: Streams a mid-size department would actually have. A site with fewer areas
#: maps these down in its adapter rather than the engine knowing about it.
STREAMS = ("resuscitation", "majors", "paediatrics", "ambulatory", "fast-track")


def route(
    *,
    level: int,
    age_band_is_paediatric: bool,
    red_flag_ids: tuple[str, ...] = (),
    needs_isolation: bool = False,
) -> RoutingDecision:
    """Recommend a stream.

    Routing is a **recommendation**, never an autonomous action: moving a
    patient to the wrong area can delay care, so its worst case is not wasted
    effort and it therefore sits outside what the system may decide alone
    (:mod:`vigil.triage.engine`, the authority ladder).
    """
    if needs_isolation:
        return RoutingDecision(
            "majors",
            "isolation required - side room in majors; do not place in open ambulatory area",
        )
    if age_band_is_paediatric:
        if level <= 2:
            return RoutingDecision(
                "resuscitation",
                "paediatric patient at high acuity - resus bay with paediatric-sized equipment",
            )
        return RoutingDecision(
            "paediatrics",
            "paediatric patient - paediatric area, age-appropriate observation and staffing",
        )
    if level == 1:
        return RoutingDecision("resuscitation", "level 1 - immediate life-saving intervention expected")
    if level == 2:
        return RoutingDecision("majors", "level 2 - should not wait; monitored bed in majors")
    if level == 3:
        if red_flag_ids:
            return RoutingDecision(
                "majors",
                f"level 3 with an unresolved red flag ({red_flag_ids[0]}) - majors rather than ambulatory",
            )
        return RoutingDecision("ambulatory", "level 3 without red flags - ambulatory assessment area")
    return RoutingDecision("fast-track", f"level {level} - single-resource pathway, fast-track")
