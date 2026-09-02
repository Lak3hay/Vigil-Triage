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

- **2026-09-02 — Summarised a scheduling result with the median alone.** The counterfactual
  reported that the median wait fell 32 minutes, which reads as "everyone waits less".
  Re-ordering conserves total waiting time — the mean was unchanged at 99.2 vs 99.1 — so
  the improvement was redistribution, and the 90th percentile had risen 50 minutes. The
  summary was true and gave the wrong impression. -> *For any policy that reallocates a
  fixed resource, report the conserved quantity and the tail beside the headline. A median
  that improves while the mean is flat is always someone else paying.*

- **2026-09-02 — Deleted slides and added new ones in the same python-pptx session, and it
  silently clobbered a kept slide.** The deleted slide's partname (`slide3.xml`) was reused by
  a newly added slide, so the team-details page was overwritten by a duplicate of the problem
  slide. Nothing raised; only a `UserWarning: Duplicate name` buried in the output. Fixed by
  saving to a temp file between the delete pass and the add pass. -> *When a library warns
  about a duplicate resource name, treat it as an error. And verify structural edits by
  listing what actually came out, not by trusting the operation.*

- **2026-09-02 — Built content slides on a layout with a dark panel baked into it.** Every
  content slide had a black block over the right third, invisible when inspecting the text
  and obvious the instant it was exported to an image. -> *Render the artefact you are
  shipping. Text extraction verifies content; only rendering verifies a document.*

- **2026-09-02 — Pushed a `demo.py` with a syntax error while 214 tests passed.** `python -m
  vigil.demo` is the FIRST command in the README, so a judge on a clean clone would have hit
  it before anything else — and the suite said nothing, because no test imported the module.
  The break came from a scripted edit that turned an escaped `\n` into a real newline inside
  a string literal. -> *A green suite is evidence about what it covers, not about what ships.
  Every user-facing entry point needs a test that actually runs it — including one real
  subprocess invocation of the exact command the documentation gives people.*

- **2026-09-02 — Shipped a public repository that did not work, and did not know for fifteen
  builds.** `.gitignore` contained an unanchored `data/`, which git matches at *any* depth, so
  the entire `src/vigil/data/` package — ten source files — was silently excluded from every
  push. On a clean clone `python -m vigil.data.fetch_demo` (the README's own command) did not
  exist and six test modules failed to import. CI had been red since the workflow was added,
  but the Lint step failed first and the Test step was `skipped` every time, so the breakage
  was never reported. -> *Ignore patterns are path patterns, not name patterns: anchor them.
  A lint gate must never be able to skip the test gate. And the only check that catches this
  class of bug is cloning your own published artefact into a clean environment and running the
  instructions you wrote for other people.*

- **2026-09-02 — Believed a linter that had never seen a third of the source.** ruff skips
  gitignored files by default, so `ruff check src tests` passed locally while ignoring the same
  package git was ignoring. Two tools agreeing because they share a blind spot is not
  confirmation. -> *When two checks depend on the same configuration, they can fail together
  silently. Verify coverage — what was actually inspected — not just the exit code.*

- **2026-09-02 — Designed a surge mode that invented capacity.** The first version halved
  high-risk re-check intervals under surge to "concentrate attention on the sickest". In a
  room of 70 patients it raised total demand from 40 to 57 re-checks an hour against a
  capacity of 8 — making the schedule *less* deliverable, which was the exact problem surge
  mode existed to solve. Caught by a test asserting the demanded rate must fall, not by
  reading the code. -> *Under scarcity you can only reallocate, never add. Any policy that
  claims to give one group more without taking it from a named other group is arithmetic
  that has not been done. Write the conservation check as a test before writing the policy.*

- **2026-09-02 — Left the mode-change message describing behaviour that had been removed.**
  The event still announced "high-risk intervals tightened" after the tightening was deleted,
  so the demo contradicted itself on screen. -> *User-facing strings are part of the
  behaviour. When the behaviour changes, grep for the words that described it.*

- **2026-09-02 — Claimed a jurisdiction and answered only half of what it obliges.** The brief
  says naming a jurisdiction *"affects your audit trail design, data retention policy, consent
  model, and what a clinician override must legally record."* We built the audit trail and the
  override record well and wrote nothing at all about retention or consent — the two we could
  not demonstrate in code, and so did not think about. -> *When a requirement lists four things,
  check off four. The ones with no natural artefact are the ones that get silently dropped.*
