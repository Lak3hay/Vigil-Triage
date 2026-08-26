# PLAN.md — Vigil

**Purpose:** the anti-drift contract. Work comes off this list; new ideas get added here
*before* being built, not after. Every item states its **done-when** condition so
"finished" is a fact, not a feeling.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done & verified ·
`[!]` blocked · `[-]` cut (with reason)

**Started:** 2026-08-26 · **Horizon:** open-ended, sequenced by phase gate rather than date

| file | its job |
|---|---|
| **`PLAN.md`** (this file) | What we intend and why. Decision rules fixed in advance. Canonical. |
| `README.md` | The front door. Update at the end of every phase. |
| `MISTAKES.md` | Append-only error log. Never edit or delete a past entry. |
| `reports/` | Results. One document owns any given number. |

---

## Status board

| phase | gate | state |
|---|---|---|
| **0 · Foundation** | clean-checkout run, CI green | **`[x]` done 2026-08-26** |
| **1 · Cohort & labels** | counts reconcile; zero patient overlap across splits | `[~]` 1.3/1.4 done |
| **2 · The floor** | B0 + NEWS2 + evaluation harness | `[ ]` |
| **3 · The model** | M1 — does trajectory beat snapshot? | `[ ]` |
| **4 · The question** | M2 — is functional form the constraint? | `[ ]` |
| **5 · The analyses** | disagreement · equity · decision curves | `[ ]` |
| **6 · The artifacts** | service · report · optional workshop submission | `[ ]` |

**Every phase from 1 onward ends in something presentable.** Phase 1 alone is a complete
analytics project; Phase 3 is a complete ML project. Everything after is upside.

### Access status

| item | state |
|---|---|
| CITI training (MIT Affiliates route) | **complete** |
| PhysioNet credentialing | **submitted 2026-08-26, awaiting review** (~1 week; the reference reply is the long pole) |
| MIMIC-IV-ED **demo** subset | **in hand** — open access, ODbL, no approval |
| MIMIC-IV-ED + MIMIC-IV full | pending credentialing |
| NHAMCS (Plan B) | not downloaded |

Nothing is blocked. The whole pipeline is built and tested on the demo subset; credentialing
changes one config path.

---

# 1. What this project is

A **landmark-based dynamic risk prediction** study on emergency-department patients:
given everything known about a patient up to minute *t* of their ED stay, predict whether
they deteriorate in the next hour.

It grew out of a competition pitch. **That framing is dead.** Voice intake, the LLM
rationale layer, the blind second opinion, the product story — all cut. They were demo
candy and they read as unfocused to a technical reader. One prediction problem, executed
rigorously, is worth more than six features executed thinly.

What survives is the part that was always load-bearing:

- **Never train on the nurse's acuity label.** Train on it and you inherit the blind spots
  the system exists to catch. Acuity is loaded for *evaluation only*.
- **Outcomes are treatment-confounded** — the patient triaged most urgently is treated
  fastest and therefore looks mild retrospectively. Choose less treatment-sensitive
  outcomes and state the residual bias rather than pretending it is solved.
- **Trends, not thresholds.** The signal is the trajectory, not any single reading.
- **The metric is not AUC** — it is sensitivity at a fixed alert budget.

## 1.1 One claim we are NOT making

The pitch described a continuous "harm-of-delay slope": how much worse your outcome gets
if you wait 30 more minutes. **That is a causal quantity and it is confounded in the
obvious direction** — sicker patients are seen sooner, so naive estimation concludes that
waiting is good for you. Recovering it needs an instrument or a natural experiment.

This project predicts **P(deterioration in the next window)**, which is a clean supervised
problem. The README says so explicitly. Naming the boundary is worth more than pretending
to have crossed it.

---

# 2. The prediction problem

**Landmark design.** For each ED stay, at landmark times *t* in {30, 60, 90, ...} minutes
after arrival:

- **Features:** everything observed in `[0, t]` — and nothing after. No exceptions.
- **Target:** did a critical event occur in `(t, t + 60min]`?
- **Sampling unit:** the **patient**. Not the stay, and certainly not the landmark row.
  One patient contributes many correlated rows; treating them as independent manufactures
  significance.

**Why landmarks rather than one prediction per stay:** the clinical question *is* dynamic.
"Should someone look at this person now?" is asked repeatedly, and a model that answers it
once at triage cannot answer it at minute 90 — which is the entire point of the project.

## 2.1 Censoring and competing risks

A stay ends when the patient is admitted, discharged, transferred, or leaves. Landmarks
after that do not exist, and a patient who leaves without being seen is **not** a patient
who did not deteriorate. The demo data contains `LEFT WITHOUT BEING SEEN` and `ELOPED`
dispositions, so this is real and must be handled explicitly, not dropped silently.

---

# 3. Cohort and labels

## 3.1 Labels — what we want

Ranked by how little they are contaminated by the treatment they trigger:

1. **ICU transfer within 24h** of ED arrival
2. **Death within 72h**
3. **Life-saving intervention** (intubation, vasopressors, transfusion) — hardest to define
4. *(weak fallback)* **Admission** vs. discharge home

**Labels 1–3 require the full MIMIC-IV hospital and ICU modules**, linked by
`subject_id`/`hadm_id`. The ED module alone supports only label 4. This is the concrete
reason credentialing matters: not for the vitals, which the demo already has, but for the
outcomes.

## 3.2 Exclusions are a measurement hazard

> *Never let a filtering step gate a measurement it could bias.*

Dropping stays with missing vitals biases the cohort toward patients well enough to be
measured — the opposite of the population of interest. **Missingness here is informative,
not random.**

**Rule: report every filter's removal rate, broken down by outcome class.** A filter that
removes 4% of survivors and 22% of deaths has stopped being a filter and become the effect.

---

# 4. Splits — three rules, all easy to get wrong

1. **Group by patient, never by stay.** Observed in the demo subset: 3.5 stays per patient,
   one patient with 23. Splitting on `stay_id` puts the same person on both sides.
2. **Temporal, never random.** Random splits leak across time and inflate everything.
3. **MIMIC dates are de-identified by a per-patient random shift** — the demo spans
   2112–2201. Absolute timestamps are not comparable across patients; use the cohort/anchor
   year grouping the dataset provides. *Verify against current MIMIC documentation before
   relying on it.*

**Done-when:** an assertion in the split code proves zero `patient_id` overlap across
train/val/test, and it is covered by a test that fails if the assertion is removed.

---

# 5. Features

Everything derives from the vitals trajectory plus static context.

| group | examples |
|---|---|
| **Last value** | most recent HR, RR, SpO2, SBP, DBP, temp at the landmark |
| **Trajectory** | delta and slope over the last 30/60 min; max/min so far; range |
| **Timing** | time since last measurement, per channel; number of measurements so far |
| **Missingness** | an indicator per channel — **a first-class feature, not a nuisance** |
| **Derived scores** | shock index (HR/SBP), NEWS2 components |
| **Static** | age, sex, arrival mode, chief complaint, prior ED visits in 90d |
| **Medication context** | beta-blocker on `medrecon` → interacts with the HR features |

## 5.1 Informative missingness is the most interesting thing in this dataset

**The fact that a vital was measured is itself a signal** — sicker patients get measured
more often, so measurement frequency encodes clinical concern that no vital value contains.
Observed missingness in the demo subset: temperature 44%, pain 29%, rhythm 97%, heart rate
2.9%. Imputing that away destroys information.

Therefore: **impute for the model's sake, but always keep the indicator.** Never
mean-impute without one.

---

# 6. The model ladder

Structured so the table tells a story regardless of which rung wins.

| rung | model | question it answers |
|---|---|---|
| **B0** | Last-observation-only logistic regression | the honest floor |
| **B1** | **NEWS2**, computed to spec | can we beat the deployed clinical standard? |
| **M1** | Gradient boosting on trajectory + missingness features | does trajectory beat snapshot? |
| **M2** | Sequence model (GRU-D / time-encoded attention) on the raw irregular series | is feature engineering the bottleneck? |
| **M3** | M1 or M2 + medication context | does masking context help? |

**Stated in advance so nobody is disappointed later: M2 will probably not beat M1.** Deep
sequence models routinely lose to gradient boosting on short stays with few measurements
(median 4 in the demo subset). **That is a result, not a failure.** "We tested whether the
functional form was the binding constraint and it was not" is a finding, and it is the
honest kind.

---

# 7. Evaluation

## 7.1 The primary metric is not AUC

> **Sensitivity for critical outcomes at a fixed alert budget.**

Both halves required. A model that catches everything by alerting on everything has solved
nothing — over-alerting consumes the attention the sick need, so it causes the very harm it
claims to prevent. **Report alert rate per nurse-hour beside every sensitivity number,
always.**

## 7.2 The headline result

> On *N* held-out stays, the trajectory model identifies *X%* of deteriorations a median of
> *Y* minutes earlier than the last-observation baseline, at *Z* alerts per nurse-hour.

## 7.3 Also reported, every time

- **Calibration** — curve + Brier score. Under a budget, calibration matters more than
  discrimination: a calibrated 0.78 beats a miscalibrated 0.84.
- **Subgroups** — sensitivity and alert rate by age band, sex, and acuity level.
  Excluding a proxy from the inputs is not evidence of fairness; proxies leak, so the audit
  is on outcomes.
- **Decision-curve analysis** — net benefit across threshold probabilities.

## 7.4 The disagreement analysis

On stays where our risk ranking and the recorded nurse acuity disagree: **who do the
outcomes side with?** Computable from historical data alone, no deployment needed, and the
most persuasive number this project can produce.

**Report both directions** — cases we escalate *and* cases we would have lowered. Reporting
only the flattering direction is the exact failure the working rules exist to prevent.

---

# 8. Pre-registered decision rules

> *Fix the decision rule before the data exist, and apply it hardest when the result lands
> just the wrong side of it. A threshold that bends at 0.055 was never a threshold.*

Written before any model is fitted.

| gate | rule | if it fails |
|---|---|---|
| **G1 · signal exists** | M1 beats B0 on lead time at matched alert rate, on held-out patients | The claim is about vitals trajectories, not our model. Report it; do not tune. |
| **G2 · clinically meaningful** | M1 beats **NEWS2** at matched alert rate | Report that a deployed clinical score is not improved on. **This is a publishable finding, not a failure.** |
| **G3 · architecture** | M2 beats M1 by a margin exceeding seed variance | Report that the functional form was not the binding constraint. Do **not** tune M2 until it wins. |
| **Guard · equity** | No stratum materially worse on sensitivity at budget | Reject regardless of headline numbers. |
| **Guard · leakage** | Zero patient overlap across splits; no feature uses data after the landmark | Any violation voids every result downstream of it. |

**What would make us wrong:** if M1 shows no lead time over B0, the trajectory thesis is
not supported and we say so. A clean negative result is a better finding than a tuned
positive one.

---

# 9. What the demo subset already told us

*Verified 2026-08-26 on MIMIC-IV-ED demo v2.2 — 222 stays, 64 patients, 1038 vital rows.
Each of these is now enforced by a test in `tests/test_schema.py`.*

1. **`vitalsign` genuinely carries repeated timestamped readings** — median 4 per stay,
   93% of stays have >= 2. **The trajectory thesis is buildable.** This was the assumption
   the whole project rested on and it is now checked rather than believed.
2. **The raw table is NOT chronologically sorted.** Any code taking "the last row" as the
   latest reading is silently wrong. Sortedness is now a schema contract.
3. **Temperature is charted in MIXED units** — mostly Fahrenheit, but 6.2% of readings are
   already Celsius. Converting the column wholesale turns a normal 36.8 C into 2.7 C, which
   then reads as a data error and is discarded; the bug deletes the most normal-looking
   observations while leaving the range looking clean. Detected per value (the plausible C
   and F ranges do not overlap). After the fix: **0 readings nulled**, range 31.4–40.5 C,
   median 36.7. Implausible readings are still **nulled, not clipped** — an impossible
   reading is absence of information, not extreme information.
4. **Missingness is large and channel-dependent** — temperature 44%, pain 29%, rhythm 97%,
   heart rate 2.9%. See §5.1.
5. **3.5 stays per patient, one patient with 23.** Patient-level grouping is mandatory.
6. **Dates are shifted** (2112–2201), confirming §4 rule 3 — and therefore **a temporal
   split is not constructible from the ED module alone.** `splits.py` refuses rather than
   producing a split that looks temporal and is not.
7. **The `triage` table is the t=0 observation and has no charttime**, so it never joins the
   vitals series unless deliberately prepended. Adding it (timestamped at `intime`) takes the
   "no observation yet at landmark" filter from **20.1% to 0%** and coverage from 198 to
   **221 of 222 stays**. A filter firing that hard was not describing the data; it was
   describing a table we had not loaded.
8. **The last observation at a landmark is old** — median staleness 59 minutes, p90 160.
   Staleness is carried as a feature: a value from three hours ago is not the same evidence
   as the same value from three minutes ago.

## 9.1 ⚠️ The demo subset is not representative — never quote a number from it

Observed: **68% admitted** (150/222) and acuity skewed to 1–3 (18 / 97 / 90 / 2 / 0), with
15 stays missing acuity entirely. A real ED admits a far smaller fraction and is dominated
by levels 3–4. The demo is a **development fixture for the pipeline, not an analysis
sample.** Any prevalence, calibration or performance number computed on it is meaningless.
*Confirm the demo's sampling method before describing the mechanism anywhere public.*

---

# 10. Phase plan

## Phase 0 — Foundation `[x]` done 2026-08-26

- [x] Repo, installable package, src layout, `.gitignore` that makes committing data hard
- [x] Canonical schema + validators shared by every loader
- [x] Dataset registry — **the source is a config value, not a code path**
- [x] MIMIC-IV-ED loader (demo + full share one implementation)
- [x] NHAMCS stub that refuses trajectory work **with a reason**, so Plan B cannot silently
      appear to work
- [x] `python -m vigil.data.fetch_demo` — one command to a runnable state
- [x] 19 tests, 7 against real data · CI on 3.10 and 3.12
- **Gate met:** clean checkout → fetch → `pytest` green.

## Phase 1 — Cohort & labels `[ ]`

- [ ] **1.1** Cohort definition + exclusion reporting **per outcome class** (§3.2)
- [ ] **1.2** Label builders for the four outcomes (§3.1); ED-only fallback documented
- [x] **1.3** Landmark table builder — leakage made structurally hard: feature code never
      touches raw vitals, only a visible slice from `expand_visible` / `attach_last_observation`,
      both of which assert the cutoff. Boundary convention fixed: an observation charted
      *at* the landmark is visible. *(2026-08-26)*
- [x] **1.4** Patient-grouped splits + the zero-overlap assertion, tested by planting a
      violation. **`patient_temporal` refuses to run without an anchor** — MIMIC shifts
      dates per patient, so `intime` cannot order patients and a split built on it would
      look temporal without being temporal. `patient_random` is the documented interim.
      *(2026-08-26)*
- [ ] **1.5** Cohort characterisation ("Table 1") → `reports/cohort.md`
- **Gate:** row counts reconcile against published totals; zero patient overlap asserted and
  tested. **Presentable as a complete analytics project.**

## Phase 2 — The floor `[ ]`

- [ ] **2.1** B0 last-observation logistic regression
- [ ] **2.2** NEWS2 implemented to spec, cross-checked against a hand-computed case
- [ ] **2.3** Evaluation harness: alert-budget curves, lead time, calibration, subgroups
- **Gate:** the baseline result stated in one sentence, with its alert rate beside it.

## Phase 3 — The model `[ ]`  ← the headline

- [ ] **3.1** Trajectory + missingness features (§5)
- [ ] **3.2** M1 gradient boosting
- [ ] **3.3** G1 and G2 evaluated as pre-registered
- **Gate:** §7.2 filled in with real numbers. **Presentable as a complete ML project.**

## Phase 4 — The question `[ ]`

- [ ] **4.1** M2 sequence model on the irregular series
- [ ] **4.2** G3 evaluated; seed variance measured *before* comparing
- **Gate:** a defensible answer either way.

## Phase 5 — The analyses `[ ]`

- [ ] **5.1** Disagreement analysis (§7.4), both directions
- [ ] **5.2** Equity audit · **5.3** Decision-curve analysis · **5.4** Ablations

## Phase 6 — The artifacts `[ ]`

- [ ] **6.1** FastAPI scorer + Dockerfile · **6.2** Small dashboard
- [ ] **6.3** `reports/paper.md` — 6–8 pages, paper-shaped
- [ ] **6.4** *Stretch:* ML4H Findings or CHIL submission

---

# 11. Risks

| # | risk | mitigation |
|---|---|---|
| R1 | Credentialing denied or slow | Everything runs on the demo subset today. Denied → NHAMCS keeps ~70% of the project (loses the trajectory headline). |
| R2 | ED module alone cannot express good labels | Known (§3.1). Full MIMIC-IV is the fix; admission-vs-home is the weak interim. |
| R3 | M1 does not beat NEWS2 | Pre-registered as a reportable finding (G2), not a failure. |
| R4 | Landmark leakage — a feature reads past *t* | The single most likely silent killer. Asserted in code and tested. |
| R5 | Scope creep back toward the product | §12. The pitch features are cut and stay cut. |
| R6 | Class imbalance makes everything look good | Never report accuracy. Alert budget and calibration are mandatory. |

---

# 12. Out of scope

Voice intake · LLM rationale generation · the blind second opinion · real-time deployment ·
hardware · EHR/FHIR integration beyond a stub · federated learning · causal estimation of
harm-of-delay (§1.1) · any novel architecture.

**Framing rule:** this is an applied clinical-ML study — the right problem formulation, the
right objective, an honest evaluation. Say so when a proposed change would enlarge the claim.

---

# 13. Working rules

Carried from the universal working rules and the Traffic-management / DSP-Lab projects.
The full text lives in those repos; these are the ones that bite *here*.

- **Falsification before confirmation.** Write the check that fails if the thing is broken,
  and watch it fail first. A check that passes on both the broken and fixed version has
  tested nothing.
- **The sampling unit is the patient**, not the row. Landmarks within a stay are an
  autocorrelated series.
- **Never compare mismatched definitions.** Match landmark to landmark, budget to budget.
- **State the averaging convention beside every number.** Mean-over-patients is not
  mean-over-landmarks.
- **Report the firing rate of every filter**, per outcome class.
- **Encode full scope in every result filename** — model, split, seed, date. Never
  `results.json`.
- **Every artifact embeds its own config.** A number without its configuration is not a
  result and must not be cited.
- **After fixing a bug, re-run every result downstream of it**, including the ones that
  still look plausible. A result that still looks right is the dangerous case.
- **State negative results first, and early.**
- **Write retractions in place.** Never leave both versions in circulation.
- **If an idiom fails twice, change the approach.** Never a third attempt.

---

# 14. How this reads on a résumé

One artifact, three framings — all true, none stretched.

- **ML / applied scientist** — "Landmark-based deterioration prediction on MIMIC-IV-ED;
  trajectory features vs. last-observation and NEWS2 baselines, evaluated at fixed alert
  budget with calibration and subgroup analysis."
- **SDE / ML engineer** — "Config-driven, tested, containerised ML pipeline over a
  credentialed clinical database; swappable data sources behind one schema; reproducible
  from clean checkout; CI-enforced."
- **Data analytics / DS** — "Cohort analysis of ED visits; quantified where recorded triage
  acuity diverges from outcome-based risk, stratified by age, sex and acuity."
