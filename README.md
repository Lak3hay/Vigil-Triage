# Vigil — deterioration prediction for emergency department patients

Predicting which waiting-room patients are getting worse, before anyone looks at them again.

Triage assigns an acuity level in the first minutes of an ED visit and then, in practice,
nobody revisits it until a bed frees. But triage is a point-in-time classification and
deterioration is a process. This project asks a dynamic version of the question instead:

> Given everything known about a patient up to minute *t* of their ED stay,
> what is the probability they deteriorate in the next hour?

**Status:** Phase 0 complete — pipeline, schema and tests. Modelling begins at Phase 1.
See [`PLAN.md`](PLAN.md) for the full plan, the pre-registered decision rules, and what is
deliberately out of scope.

---

## Quickstart

```bash
git clone <this repo> && cd vigil
pip install -e ".[dev]"

python -m vigil.data.fetch_demo   # open-access MIMIC-IV-ED demo, ~90 KB, no account needed
pytest                            # 19 tests, 7 against the real data
```

```python
from vigil.data import get_dataset

ds = get_dataset("mimic-iv-ed", root="data/raw/mimic-iv-ed-demo")
vitals = ds.vitals()      # canonical schema, Celsius, sorted, implausible values nulled
print(ds.plausibility_report)
```

## Data

| source | access | supports |
|---|---|---|
| **MIMIC-IV-ED demo** (v2.2) | open, ODbL, no account | the whole pipeline — 222 stays |
| **MIMIC-IV-ED + MIMIC-IV** | PhysioNet credentialing | scale, and the outcome labels |
| **NHAMCS** | public | cross-sectional analyses only — *no serial vitals* |

**No data is committed, ever.** MIMIC's DUA forbids redistribution and `.gitignore` is
written to make it hard to do by accident. The demo subset is fetched by script.

The data source is a **config value, not a code path** — every loader normalises into one
canonical schema (`vigil.data.schema`), so swapping sources changes a string.

## What the data made us fix

Checking the demo subset before writing feature code turned up four traps. Each is now
enforced by a test rather than a comment:

1. **`vitalsign` is not chronologically sorted.** Taking "the last row" as the latest
   reading is silently wrong. Sortedness is a schema contract the loader must satisfy.
2. **Temperature is Fahrenheit**, and the range includes an impossible 31.4 °F. Implausible
   values are **nulled, not clipped** — an impossible reading is absence of information,
   not extreme information. Every filter reports its firing rate.
3. **Missingness is large and channel-dependent** (temperature 44%, rhythm 97%, heart rate
   2.9%) — and **informative**: sicker patients get measured more often, so measurement
   frequency carries signal no value contains. Missingness indicators are features, not
   nuisances.
4. **3.5 stays per patient** (one patient has 23), so splits group on patient, never on
   stay.

## Design commitments

- **Never train on the nurse's acuity label.** Train on it and you inherit the blind spots
  the system exists to catch. Acuity is loaded for evaluation only.
- **Outcomes are treatment-confounded** — the most urgently triaged patient is treated
  fastest and so looks mild retrospectively. We prefer less treatment-sensitive outcomes and
  state the residual bias rather than claiming it away.
- **The metric is not AUC.** It is sensitivity at a fixed alert budget, with alert rate
  reported beside every sensitivity number. A model that catches everything by alerting on
  everything has solved nothing.
- **Decision rules are pre-registered** ([`PLAN.md` §8](PLAN.md)) before any model is fitted,
  including what result would make us wrong.

## What this project does not claim

An earlier version of this idea described a continuous "harm-of-delay" curve — how much
worse your outcome gets if you wait longer. **That is a causal quantity and it is confounded
in the obvious direction:** sicker patients are seen sooner, so naive estimation concludes
waiting is good for you. Recovering it needs an instrument or a natural experiment.

This project predicts P(deterioration in the next window), which is a clean supervised
problem, and stops there.

## Layout

```
src/vigil/
  data/      loaders + canonical schema + registry
  features/  landmark features, trajectory + missingness   (Phase 1)
  models/    the B0 -> M3 ladder behind one interface       (Phase 2-4)
  eval/      alert-budget curves, calibration, subgroups    (Phase 2)
tests/       contract tests; the data ones skip if absent
reports/     results — one document owns any given number
```

## Licence & attribution

Code: MIT. Data: not included — MIMIC-IV-ED is © its authors under the PhysioNet
Credentialed Health Data Use Agreement; the demo subset is ODbL.
