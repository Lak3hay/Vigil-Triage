# PLAN.md — Vigil

**What this repository is, what it deliberately is not, and what would come next.**

Accenture Innovation Challenge 2026 · Round 2 · Problem Track 2 — *PatientTriage.ai*
Companion documents: [`README.md`](README.md) (the front door),
[`MISTAKES.md`](MISTAKES.md) (append-only error log).

> **Note on history.** Until 2 Sep this file described a *machine-learning research
> project* — a landmark-based deterioration model on MIMIC-IV-ED, with a phased
> B0→M3 baseline ladder. That was a genuine plan, written before the Round 2 brief
> arrived, and it has been replaced rather than quietly edited: a canonical-looking
> plan describing a different project than the README is worse than no plan at all.
> The research direction survives in §6, correctly labelled as future work, because
> the reasoning in it is still right — it is simply not what was built here.

---

## 1. Scope

A **working prototype of a triage assistant**, demonstrating the core mechanism on
synthetic patients, plus the business proposal that surrounds it.

| In scope | Deliberately out |
|---|---|
| Deterministic clinical engine, age-banded | Any learned model in the decision path |
| Encoded red-flag panel | An LLM computing a score |
| WATCH loop — clock + trend | Real-time deployment, hardware |
| Cost-of-waiting queue policy, routing | Causal estimation of harm-of-delay |
| Surge mode | EHR / bed / roster integrations (boundary only) |
| Tamper-evident audit trail | Clinical validation of any kind |

**Framing rule:** this is an applied systems contribution — the right decomposition
of authority, the right objective, an honest evaluation. Say so when a proposed
change would enlarge that claim.

## 2. The design commitments

These are the load-bearing ideas. Each is enforced in code and tested, not asserted
in prose.

1. **Safety-monotone.** `recommended_level <= nurse_acuity`, swept over 12,487
   synthetic patients. Vigil can only ever raise urgency, so adding it cannot create
   a new under-triage failure that did not already exist without it.
2. **Only reversible actions are autonomous.** The system decides the reassessment
   clock, observation intensity, order within a band, and the operating mode. Each
   has the property that its worst case is wasted effort, never missed care. Acuity
   is only ever *recommended*.
3. **Age band before anything else.** NEWS2 for adults, a PEWS-style score over
   paediatric ranges for children. An unknown age lowers confidence; it is never a
   silent adult default.
4. **The lethal presentations are encoded, not learned.** They are rare *by
   construction*, so a model trained on ED data will underperform on exactly the
   cases that kill. Twelve rules in one file a governance lead can review.
5. **Uncertainty is decomposed and never relaxes anything.** Four named factors, each
   with the action that would raise it. Low confidence escalates and tightens the
   clock; it never de-escalates.
6. **The cost-of-waiting curve is a declared policy, not an estimated quantity.**
   See §6.1 for why the causal version is not attempted.
7. **Under scarcity you can only reallocate, never add.** Surge stretches low-risk
   intervals and holds the sickest unchanged. It tightens nothing, because there are
   fewer clinician-minutes under surge, not more.

## 3. Status

| Component | State |
|---|---|
| Clinical engine — age bands, scores, red flags, confidence | **done** |
| WATCH loop — clock, trend detection, escalation | **done** |
| Flow — cost-of-waiting, routing, site profiles | **done** |
| Surge mode | **done** |
| Audit — hash-chained, retention, consent basis | **done** |
| Synthetic cohort, surge generation, counterfactual | **done** |
| Live board (GitHub Pages) | **done** |
| Dataset loaders + cohort/landmark/split machinery | **done**, unused by the prototype |
| Integration adapters | **boundary published, none implemented** (§5) |
| LLM layer | **designed, not built** (§5) |
| Learned model in the decision path | **not built** (§6) |

307 tests · lint clean · CI green · reproducible from a clean clone.

## 4. Decision rules fixed in advance

Written before the results existed, kept so they cannot be adjusted to taste.

| Rule | Consequence if it fails |
|---|---|
| The safety property holds for every patient in the sweep | The central design claim is false. Report it; do not weaken the sweep. |
| A fully normal adult is **not** escalated | We have built an alarm, not an assistant. |
| The counterfactual's **mean** wait is conserved | We are measuring throughput, not sequencing, and the result means nothing. |
| Surge **reduces** the demanded re-check rate | Surge is inventing capacity. *(This one fired: see MISTAKES.md, 2026-09-02.)* |
| Every filter reports its firing rate | An unreported filter has become the effect being measured. |

## 5. Designed and not built — with the boundary published

Naming these precisely is the point; a prototype that hides its edges cannot be
evaluated.

- **Integrations.** [`integrations.py`](src/vigil/integrations.py) publishes the exact
  question Vigil would ask a patient-record, bed-management or staff-roster system,
  the answer it needs, and what it does when the answer never comes. Four capability
  tiers, T0–T3; we run at T0 and say so. Most of the value lands at T1 —
  deliberately, because a system whose benefit requires T3 is one nobody can deploy.
- **LLM layer.** Free-text intake structuring and narrative rationale. It never
  computes a score, in any planned version. The system runs fully without it.

## 6. Beyond Round 2 — the research direction

Retained because the reasoning is still correct and Round 3 is likely to probe it.
**None of this is implemented, and no claim in this repository depends on it.**

### 6.1 What we do not claim, and why

The cost-of-waiting curve is a **scheduling policy**. The causal version — *how much
worse is your outcome if you wait thirty more minutes* — is confounded in the obvious
direction: sicker patients are seen sooner, so naive estimation concludes that
waiting is good for you. Recovering it honestly needs an instrument or a natural
experiment, and we have neither.

### 6.2 If a learned component were added

It would be a **landmark-based deterioration model**: at each landmark *t*, using only
data observed in `[0, t]`, predict a critical event in `(t, t+60min]`. The machinery
exists and is tested — cohort building with per-outcome exclusion reporting, landmark
construction with leakage assertions, patient-grouped splits.

Three traps, worth stating because they are the ones that sink such projects:

1. **Never train on the nurse's acuity label** — you inherit the blind spots the
   system exists to catch.
2. **Outcomes are treatment-confounded.** The patient triaged most urgently is
   treated fastest and looks mild retrospectively. Prefer less treatment-sensitive
   outcomes and state the residual bias rather than claiming it away.
3. **Group splits by patient, split temporally.** MIMIC shifts dates per patient, so
   `intime` cannot order patients — [`splits.py`](src/vigil/data/splits.py) refuses
   rather than producing a split that looks temporal and is not.

**And the constraint that governs all of it:** a learned model may only ever *raise*
urgency. It is not permitted to suppress anything the deterministic layer flags. The
safety property is not negotiable by a model that scores well.

## 7. Working rules

The discipline this project holds itself to. The full record of where it failed is in
[`MISTAKES.md`](MISTAKES.md) — twelve entries, all found by our own checks.

- **Falsification before confirmation.** Write the check that fails if the thing is
  broken, and watch it fail first.
- **A green suite is evidence about what it covers, not about what ships.** Test the
  entry point a user actually runs, in a real subprocess.
- **Verify the published artefact, not the working copy.** Clone your own repository
  into a clean environment and run the instructions you wrote for other people.
- **Report the conserved quantity and the tail beside any headline.** A median that
  improves while the mean is flat is always someone else paying.
- **When two checks share a configuration they can fail together silently.** Verify
  what was actually inspected, not just the exit code.
- **State the horizon beside any asymptotic claim.** A property true only at a
  timescale nobody experiences is not a feature.
- **Count, never type.** Every number in the README and the deck is derived at build
  time; three had already drifted before that rule existed.
- **Write retractions in place.** This file is one.
