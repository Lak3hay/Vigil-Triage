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
