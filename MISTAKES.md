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
