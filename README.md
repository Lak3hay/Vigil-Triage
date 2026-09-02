# Vigil

**A triage assistant that never stops watching.**

Accenture Innovation Challenge 2026 · Round 2 · Problem Track 2 — *PatientTriage.ai* · **Team Vigil**
> ### 🎬 [**Watch the Round 2 Demo Video**]( https://drive.google.com/file/d/1Gj7AQwzO8Vr8WbLXPL6RtKO4q2SMLXsn/view?usp=sharing )
> ### ▶ [**Open the live board — no install**](https://lak3hay.github.io/Vigil-Triage/)
> Press **Play** and watch **P17**. She arrives at level 3 with every vital sign inside its
> normal range, and the nurse is right to call her level 3. Forty minutes later Vigil flags
> her as deteriorating — while *every reading is still normal* — tightens her re-check clock
> from 6 hours to 15 minutes, and she climbs from 9th in the queue to 1st.
> Toggle **Status quo** to see where she would have been.

---

## The problem, in one paragraph

Triage assigns an acuity level in the first ninety seconds of an emergency visit, and then in
practice **nobody revisits it**. But triage is treated as an event while deterioration is a
process. A patient marked stable at minute one can be in septic shock by minute ninety, and
the readings that would have shown it — a heart rate drifting up, a saturation drifting down —
never cross a threshold, so nothing fires. Meanwhile crowding is documented to push acuity
assignments *downward*: the system under-triages hardest exactly when accuracy matters most.

Vigil does not compete with the nurse on the average patient. **On the average patient the
nurse is already right.** It covers the tail, and it never looks away.

---

## What it does

| Loop | What it does | Authority |
|---|---|---|
| **Assess** | Resolves the **age band first**, then scores with the right instrument — NEWS2 for adults, a PEWS-style score over PALS ranges for children. Runs a panel of 12 encoded red flags for presentations too rare to learn. | Recommends |
| **Watch** | Every waiting patient stays live. Two triggers: an overdue re-check clock, and **worsening re-recorded vitals**. Trends, not thresholds. | Decides the clock |
| **Sequence** | Ranks the queue by **cost of waiting** — a second axis alongside acuity — and recommends a stream. | Decides order within a band |
| **Surge** | When the re-check schedule the department *owes* exceeds what it can *deliver*, low-risk intervals and low-acuity targets stretch, the alert floor rises, and sub-threshold events batch to the charge nurse. The sickest keep their cadence exactly; nothing is tightened, because surge does not create clinician-minutes. | Decides the mode, and logs it |

### The one idea everything else falls out of

Acuity asks *how sick are you*. Vigil adds a second, continuous axis: **what does waiting cost
you?** From that single curve you get sequencing within a band, waiting-room re-ranking when
someone deteriorates, anti-starvation, and routing — not as four features, but as one equation.

It is a **declared scheduling policy, not an estimated causal quantity.** We do not learn "how
much worse will your outcome be if you wait 30 more minutes": that is confounded in the obvious
direction, since sicker patients are seen sooner. Every constant is visible in
[`flow/policy.py`](src/vigil/flow/policy.py) and site-configurable, which is also how the same
engine flexes from a 120-visit rural department to a 500-visit urban one **without retraining
anything**.

---

## The safety property

The brief requires a solution *"deliberately tuned to bias toward escalation under uncertainty"*
and says teams **must demonstrate this design choice explicitly**. So we do not claim it — we
test it:

```
recommended_level  <=  nurse_acuity          for every patient, always
```

Swept over **12,487** synthetic patients spanning every age band, every vital-sign extreme and every
level of data completeness ([`tests/test_safety_property.py`](tests/test_safety_property.py)).

**Why that one inequality matters:** if Vigil can only ever *raise* urgency, then adding it to a
department **cannot create a new under-triage failure that did not already exist without it**.
The system is safety-monotone. Its worst case is wasted effort, never missed care.

The suite is adversarial in both directions. A fully-recorded, entirely normal adult must
**not** be escalated — otherwise we have built an alarm, not an assistant.

---

## Results

One shift, run twice. Identical arrivals, identical deteriorations, identical capacity.
**Only the queue ordering changes.** `python -m vigil.demo --experiment`

| | Status quo (FIFO) | Vigil |
|---|---|---|
| Patients seen | 106 | 106 |
| **Mean** wait | 99.2 min | **99.1 min** |
| Median wait | 100.1 min | **68.2 min** |
| 90th-percentile wait | 175.2 min | **224.8 min** |
| **Median deterioration → seen** | **62.4 min** | **17.9 min** |

Paired on the **54 patients who deteriorated in both arms**: median **−50 min**, with
**37 reached sooner, 0 later, 17 unchanged.**

### Read the mean row first

Re-ordering cannot create capacity. The mean wait is unchanged — **total waiting time is
conserved and only redistributed.** The median falls 32 minutes while the 90th percentile rises
50. Most patients wait considerably less; a minority wait meaningfully longer. That is the
trade, and the anti-starvation property is what stops the tail growing without bound.

> Simulation on synthetic patients under stated assumptions. **Not clinical evidence.** The
> assumptions are parameters, not constants — re-run with different ones to test whether the
> direction survives.

---

## Round 2 rubric — where each requirement is demonstrated

Run `python -m vigil.demo --rubric` to have the system verify these **live at run time**.

| Requirement | Where | Evidence |
|---|---|---|
| Triage scoring on 15–20 records | `--patients` | 20 synthetic patients, 60% with a prior record |
| Ambiguous / paediatric-geriatric / zero-history | `--cases` | 5 ambiguous · 3 paediatric · 4 geriatric · 8 zero-history |
| Behaviour under 3× surge | `--surge` | 63/hr vs ~21 normal; 17% of triage degraded by crowding |
| No score without a confidence indicator | `--patients` | 4 decomposed factors on every assessment; 1 abstains outright |
| Clinician override + what is logged | `--audit` | Hash-chained log; override keeps the reasoning it overruled |
| Age-banded, not one adult model | `--cases` | NEWS2 vs PEWS-style; the infant control must **not** escalate |
| Escalation bias, demonstrated | `pytest` | 12,487-patient sweep of the safety property |
| Waiting-queue monitoring, both triggers | `--watch` | Overdue clock **and** worsening re-recorded vitals |
| Scalability across hospitals | `--profiles` | Two site profiles + four capability tiers, no retraining |
| Surge changes behaviour, not just volume | `--surge` | Re-check demand 46→40/hr; interrupts 187→51; invariants tested |
| Explainable within seconds | `one_line` | A one-sentence version of every assessment |
| Integration with existing systems | [`integrations.py`](src/vigil/integrations.py) | Published adapter boundary; T0–T3 capability tiers |
| Stated regulatory jurisdiction | [`audit/log.py`](src/vigil/audit/log.py) | India — DPDP Act 2023 + ABDM |

---

## Implementation approach

**Deterministic first, learned second, generative last — and never for a number.**

- **Clinical logic is deterministic and offline.** Same input, same output, no network, no
  model server. A score a regulator cannot reproduce is not usable in a clinical workflow, and
  a recommendation nobody can re-derive is not reviewable.
- **The lethal presentations are encoded, not learned.** Occult sepsis in the elderly, atypical
  ACS, posterior stroke, compensated shock in children, beta-blocker masking. These are rare
  *by construction* — a model trained on ED data will underperform on exactly the cases that
  kill, because they are underrepresented in the training set by definition. No amount of data
  fixes that. So they live in [one readable file](src/vigil/clinical/redflags.py) a clinical
  governance lead can review and sign off, each carrying its rationale **and why it gets missed**.
- **Uncertainty is decomposed, not a single number** — completeness, recency, population fit,
  coherence, each with the concrete action that would raise it. Low confidence *escalates*; it
  never de-escalates and never silently downgrades.

### What the LLM does not do

**It never computes a score.** In the obvious build, a language model is asked "what ESI level
is this patient?" — the output is unreproducible, unauditable, and cannot be signed off by
anyone. Vigil confines language models to language: structuring free-text intake, and
describing a decision the deterministic layer has already made. That layer is designed and
specified but **not built** in this prototype, and the system runs fully without it.

## Solution architecture

```
                 ┌──────────────────────────────────────────┐
 intake  ──────▶ │  triage/snapshot   what is known, now    │
                 └───────────────────┬──────────────────────┘
                                     ▼
       ┌───────────────────── triage/engine ─────────────────────┐
       │  1. clinical/agebands   resolve band FIRST              │
       │  2. clinical/scores     NEWS2  |  PEWS-style            │
       │  3. clinical/redflags   12 encoded presentations        │
       │  4. triage/confidence   4 factors, or ABSTAIN           │
       │  5. compose  →  min(nurse, computed)   ← safety property│
       └───────────────────┬─────────────────────────────────────┘
                           ▼
        ┌──────────── flow/room  the live waiting room ──────────┐
        │  flow/watch    trend detection (2 readings is enough)  │
        │  flow/policy   cost of waiting · routing · profiles    │
        └───────────────────┬────────────────────────────────────┘
                            ▼
              audit/log   hash-chained, append-only
                            ▼
              board.py  →  docs/index.html   (GitHub Pages)
```

| Module | Responsibility |
|---|---|
| [`clinical/`](src/vigil/clinical) | Age bands, early-warning scores, red-flag panel |
| [`triage/`](src/vigil/triage) | Snapshot, confidence, and the engine that composes them |
| [`flow/`](src/vigil/flow) | Cost-of-waiting policy, routing, the WATCH loop, site profiles |
| [`audit/`](src/vigil/audit) | Tamper-evident log; DPDP-shaped data minimisation |
| [`sim/`](src/vigil/sim) | 20 hand-written patients, surge generation, the counterfactual |
| [`data/`](src/vigil/data) | Real-dataset loaders behind one canonical schema |

## Dependencies

Python **3.10+**, and four libraries — `pandas`, `numpy`, `pyarrow`, `scikit-learn` — plus
`pyyaml` and `tabulate`. Full list in [`pyproject.toml`](pyproject.toml).

**The prototype itself needs no network, no API key, no GPU and no database.** The published
board is a single static HTML file with zero external requests.

## Execution instructions

```bash
git clone https://github.com/Lak3hay/Vigil-Triage.git
cd Vigil-Triage
pip install -e ".[dev]"

python -m vigil.demo            # the whole thing
python -m vigil.demo --rubric   # the brief's checklist, verified live
pytest                          # 307 tests
python -m vigil.board           # regenerate docs/index.html
```

Optional — run the loaders against real de-identified clinical data
(MIMIC-IV-ED demo subset, open access, ODbL, ~90 KB, no account needed):

```bash
python -m vigil.data.fetch_demo
pytest tests/test_mimic_loader.py
```

---

## Regulatory posture

**Assumed jurisdiction: India — Digital Personal Data Protection Act 2023, with ABDM/ABHA as
the health-record layer.**

- **Clinical decision support, not a diagnostic device.** Never autonomous on acuity, always
  overridable, every suggestion and override logged.
- **Tamper-evident audit chain.** Each entry carries the hash of the one before it; altering any
  past entry breaks every subsequent hash and `verify()` names the index. This is tamper-*evident*,
  **not tamper-proof** — a full rewrite still verifies, and [a test asserts exactly
  that](tests/test_audit.py) so the claim in the code matches the claim in this README.
  Tamper-proofing needs an external anchor, which is a deployment decision.
- **Retention — two clocks, because these records answer different questions.** The clinical
  decision record is part of the patient's episode of care and is retained with it; Vigil does
  not set that period and must not shorten it, because it is evidence in exactly the disputes
  an audit trail exists for. Operational telemetry and the disagreement record aggregate at 90
  days, with per-patient rows discarded and no link to an individual clinician beyond that.
- **Consent — the lawful basis, not just the safeguards.** Assessment and monitoring sit on the
  same basis as the care episode they support: Vigil asks for nothing the department was not
  already recording, which is why the snapshot is scoped to observations a nurse takes anyway.
  **Model improvement is a genuinely new purpose and is *not* covered by that basis** — it
  needs notice and consent, or aggregation past the point of being personal data. The record is
  inspectable and contestable by the patient; an automated recommendation nobody can see is not
  one they can contest.
- **Data minimisation.** The log records the *reasoning*, not the person — a pseudonymous
  reference, the inputs that drove the decision, and the decision. Names and identifiers stay in
  the hospital record system where consent already governs them.
- **Fairness, not only security.** The disagreement record — every time a clinician's level
  differs from the recommendation — exists to improve the model and audit the system. It must
  **never** be repurposed for performance management of individual staff. A tool nurses believe
  is scoring them will be worked around within a week, so this is a design constraint as much as
  an ethical one, and in deployment it belongs in the contract.

## What we did **not** build

Stated plainly, because a prototype that hides its edges cannot be evaluated.

- **No causal estimate of harm-of-delay.** The cost-of-waiting curve is a declared policy. The
  causal version needs an instrument or a natural experiment.
- **No learned model in the decision path.** The data pipeline for it exists and is tested;
  training was cut for scope, and the ED module alone cannot express good outcome labels
  (ICU-within-24h and mortality need the MIMIC-IV hospital modules, which need credentialing).
- **No LLM layer.** Designed, specified, not built — see above.
- **No integrations are implemented** — but the boundary is published
  ([`integrations.py`](src/vigil/integrations.py)): the exact question Vigil would ask a
  patient-record, bed-management or staff-roster system, the answer it needs, and what it does
  when the answer never comes. An integration team can read it and say "we can supply that in a
  fortnight" or "we cannot", which is the conversation that decides whether a deployment
  happens. Vigil runs today at **Tier 0** — nothing integrated — and says so; most of the value
  arrives at Tier 1, deliberately, because a system whose benefit requires Tier 3 is one almost
  nobody can deploy.
- **No clinical validation.** Every number here comes from synthetic patients or a simulation.
  Nothing in this repository is evidence of clinical benefit, and the thresholds are illustrative
  defaults that a real deployment would recalibrate against its own case mix.

## Working notes

[`MISTAKES.md`](MISTAKES.md) is an append-only log of every error we made building this — six of
them found by our own checks, including a detector that flagged deterioration and then scheduled
the re-check four hours later, and a summary that reported an improved median while the mean was
flat. It is public on purpose.

---

**Team Vigil** — Lakshay · Ishan Shukla
Code MIT. Not a medical device. Synthetic patients throughout; no real person is represented.
