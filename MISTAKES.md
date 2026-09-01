# Mistakes log

**Append-only. Never edit or delete a past entry.** Format: *what happened -> the rule.*
Kept so the same class of error is not repeated.

## Carried in from earlier work

Classes already paid for on other projects and most likely to recur here.

- Diagnosed a cause and built the fix without testing the hypothesis first.
- Asserted a claim from a descriptive ranking rather than a test at the sampling-unit level.
- Published a conclusion from a check that could not fail.
- Justified a claim with a test structurally incapable of testing it.
- Trained in one regime and evaluated in another.
- Attached a causal story to a real correlation and was wrong.
- Fixed a bug and did not re-run every result downstream of it.
- Reused stale checkpoints written under the bug that was just fixed.
- Overwrote a primary result file with a partial run, because the filename did not encode scope.
- Recomputed an existing number under a different averaging convention.
- Claimed a tie from a large p-value without an equivalence bound.
- Repeated a failing idiom three times instead of changing the approach.

## This project

- **2026-08-26 — Shipped a slide claiming "every reading still normal" beside vitals of
  HR 104 and SpO2 93, both of which are outside the normal range.** The example contradicted
  the very claim it was illustrating. -> *When an exhibit is the evidence for a claim, check
  the exhibit against the claim literally, value by value. A number chosen for rhetorical
  effect is still a number someone will check.*

- **2026-08-26 — Converted the whole MIMIC-ED temperature column from Fahrenheit when it
  is charted in MIXED units.** 6.2% of readings were already Celsius; converting them
  turned a normal 36.8 C into 2.7 C, which the plausibility filter then discarded as a
  data error. The bug deleted the most *normal-looking* observations and left the range
  looking clean, so nothing downstream would have complained. -> *Never assume a units
  column is homogeneous. Detect per value where the plausible ranges do not overlap, and
  look at what a filter actually removed rather than only that it removed something.*

- **2026-08-26 — Nearly shipped a landmark builder that silently dropped 20% of prediction
  moments as "no observation yet".** The triage table holds each stay's first vitals but
  no charttime, so it never entered the vitals series; every stay's first observation was
  therefore later than reality. The filter looked like housekeeping and was in fact
  excluding early landmarks for exactly the patients who were re-checked slowly. -> *When
  a filter fires on a large fraction, the first question is whether the data is missing or
  whether you failed to load it.*

- **2026-08-26 — Built a stratified-exclusion audit that divided landmark counts by stay
  counts.** Landmark filters record `stay_id` (one stay contributes many landmarks), so the
  numerator counted landmarks and the denominator counted stays. The output looked like a
  percentage, sorted like a percentage, and reported 100% for a filter that had removed
  155 landmarks from 155 stays. Caught only because the number was implausibly round. ->
  *A rate is only meaningful when numerator and denominator count the same unit. Where ids
  can repeat, make the function demand an explicit denominator rather than inferring one.*

- **2026-09-02 — Detected deterioration and then handed the patient a four-hour re-check
  clock.** The monitoring interval was derived from the early-warning score alone, but the
  whole point of trend detection is that the *score can still be low while the trajectory is
  the finding*. The system would have said "deteriorating - reassess within 240 minutes."
  -> *When a module adds a new signal, check every downstream decision that was derived
  before that signal existed. A correct detector wired into a stale policy is still a
  wrong system.*

- **2026-09-02 — Wrote an anti-starvation test asserting a property the design should not
  have.** The test demanded that any level eventually overtake any other, including level 1.
  The code was right and the test was wrong: a queue of minor complaints outranking a
  cardiac arrest is not fairness. Level 1 is now an explicit separate priority class rather
  than a point on the curve. -> *When a test fails, first ask whether the property being
  asserted is one you actually want. A test can encode a bug as confidently as code can.*

- **2026-09-02 — Nearly published an anti-starvation guarantee that was practically
  vacuous.** Convexity does guarantee every waiting level overtakes every other eventually,
  but for distant pairs "eventually" is 30 hours to 4.5 days - irrelevant inside a real ED
  stay. The meaningful guarantee is adjacent-level overtaking within one shift. -> *State
  the horizon beside any asymptotic claim. A property that is true only at a timescale
  nobody experiences is not a feature.*

- **2026-09-02 — Wrote a counterfactual that compared two arms on different denominators.**
  A patient seen before their trajectory fires never deteriorates in that arm, so FIFO had
  43 deteriorating patients and Vigil had 49 — and comparing arm-level medians silently
  compared different populations. It would have flattered whichever arm left more sick
  people waiting. Now paired on the 36 patients who deteriorated in *both* arms, matched by
  id, with the arm-only counts reported. -> *When two arms can change who ends up in the
  measurement set, the comparison must be paired on the units present in both. An unpaired
  median across differing denominators is the "filter that gates its own measurement"
  failure wearing a different hat.*

- **2026-09-02 — Built a demo waiting room far sicker than any real one.** The surge
  generator sampled uniformly from the hand-written cohort, which is *deliberately* loaded
  with hard cases — so ~60% of simulated arrivals escalated and the board filled with
  level-1 patients. That is not just unrealistic: it makes the queue policy look useless,
  because when everyone is critical there is nothing left to re-order. Now sampled against
  a plausible ED case mix (2% L1 · 7% L2 · 31% L3 · 42% L4 · 18% L5). -> *A fixture built
  to stress one component is the wrong population to sample when simulating the whole
  system. Check the simulated distribution against the real-world one before drawing any
  conclusion from it.*
