# Cohort report

*Generated 2026-08-26 · dataset `mimic-iv-ed` · root `data/raw/mimic-iv-ed-demo`*

> ⚠️ **Built on the MIMIC-IV-ED demo subset, which is not representative.**
> It is 68% admitted with acuity skewed to levels 1-3; a real ED is neither.
> This report demonstrates that the pipeline runs and that the audit machinery
> works. **No prevalence, rate or performance figure here should be quoted.**

## Configuration

```
CohortSpec   {'min_age': None, 'max_age': None, 'min_stay_minutes': 30, 'max_stay_hours': 72.0, 'require_any_observation': True}
LandmarkSpec {'grid_minutes': (30, 60, 90, 120, 180, 240, 360), 'horizon_minutes': 60, 'require_prior_observation': True}
SplitSpec    {'strategy': 'patient_random', 'fractions': (0.7, 0.15, 0.15), 'seed': 42}
```

## 1. Exclusions

```
[build_cohort] 222 -> 219 stays
    stay shorter than 30 min (no landmark possible)        1  (  0.5%)
    stay longer than 72 h (administrative artifact)        2  (  0.9%)
    RETAINED                                             219  ( 98.6%)
```

### Stratified by outcome

The column to read is `disparity` -- the widest gap in removal rate between
classes. A filter with a large disparity is selecting on the outcome and has
stopped being a filter.

**Interim outcome is admission vs. discharge**, the only label expressible from
the ED module alone. ICU-within-24h and death-within-72h need the MIMIC-IV
hospital and ICU modules (PLAN.md 3.1), and this audit should be re-run against
them once available -- admission is a weak proxy and may hide a disparity that a
clinical outcome would expose.

| reason                                          |   n[admitted] |   pct[admitted] |   n[not admitted] |   pct[not admitted] |   disparity |
|:------------------------------------------------|--------------:|----------------:|------------------:|--------------------:|------------:|
| stay shorter than 30 min (no landmark possible) |             0 |            0    |                 1 |                1.49 |        1.49 |
| stay longer than 72 h (administrative artifact) |             1 |            0.65 |                 1 |                1.49 |        0.85 |

## 2. Table 1

|                                     | all            | ADMITTED        | ELOPED        | HOME           | LEFT AGAINST MEDICAL ADVICE        | LEFT WITHOUT BEING SEEN        | OTHER         | TRANSFER        |
|:------------------------------------|:---------------|:----------------|:--------------|:---------------|:-----------------------------------|:-------------------------------|:--------------|:----------------|
| stays                               | 219            | 150             | 1             | 59             | 2                                  | 2                              | 1             | 4               |
| patients                            | 64             | 60              | 1             | 26             | 2                                  | 1                              | 1             | 3               |
| stays per patient                   | 3.42           | 2.5             | 1.0           | 2.27           | 1.0                                | 2.0                            | 1.0           | 1.33            |
| stay hours, median [IQR]            | 5.8 [4.2-8.7]  | 5.6 [4.0-7.8]   | 5.8 [5.8-5.8] | 6.6 [5.0-10.9] | 7.3 [7.2-7.4]                      | 3.6 [2.9-4.3]                  | 3.1 [3.1-3.1] | 12.0 [9.1-22.9] |
| disposition, most common            | ADMITTED (68%) | ADMITTED (100%) | ELOPED (100%) | HOME (100%)    | LEFT AGAINST MEDICAL ADVICE (100%) | LEFT WITHOUT BEING SEEN (100%) | OTHER (100%)  | TRANSFER (100%) |
| censored (LWBS/eloped/AMA)          | 5 (2.3%)       | 0 (0.0%)        | 1 (100.0%)    | 0 (0.0%)       | 2 (100.0%)                         | 2 (100.0%)                     | 0 (0.0%)      | 0 (0.0%)        |
| observations per stay, median [IQR] | 5 [3-7]        | 5 [4-7]         | 2 [2-2]       | 4 [3-6]        | 4 [4-4]                            | 2 [2-3]                        | 1 [1-1]       | 6 [4-8]         |

## 3. Missingness by channel

Two different questions: the share of *observations* lacking a channel, and the
share of *stays* where it was never recorded at all. A channel measured once and
then never again is present at the stay level and absent for most of the
trajectory. Missingness is a feature here, not a nuisance (PLAN.md 5.1).

| channel   |   pct_observations_missing |   pct_stays_never_recorded |
|:----------|---------------------------:|---------------------------:|
| temp_c    |                       39.1 |                        5.9 |
| spo2      |                        7.2 |                        4.1 |
| rr        |                        5.6 |                        4.1 |
| sbp       |                        5.1 |                        4.1 |
| dbp       |                        5.1 |                        4.1 |
| hr        |                        4.3 |                        4.1 |

## 4. Landmarks

```
[build_landmarks] 1533 -> 1327 landmarks
    landmark at or after discharge      206  ( 13.4%)
    no observation yet at landmark        0  (  0.0%)
    RETAINED                           1327  ( 86.6%)
```

Landmark drops are counted per landmark but keyed by stay, so the audit needs an
explicit denominator (eligible slots per class) rather than a per-stay one --
otherwise the numerator counts landmarks and the denominator counts stays.

| reason                         |   n[admitted] |   pct[admitted] |   n[not admitted] |   pct[not admitted] |   disparity |
|:-------------------------------|--------------:|----------------:|------------------:|--------------------:|------------:|
| landmark at or after discharge |           155 |           14.38 |                51 |               11.21 |        3.17 |
| no observation yet at landmark |             0 |            0    |                 0 |                0    |        0    |

## 5. Splits

Strategy `patient_random`. **Not temporal** -- MIMIC shifts dates per patient, so `intime` cannot order
patients and a split built on it would look temporal without being temporal.
A true temporal split needs `patients.anchor_year_group` from the hospital
module (PLAN.md 4). Any result computed on this split must say so.

| split   |   rows |   stays |   patients |   pct_patients |
|:--------|-------:|--------:|-----------:|---------------:|
| train   |   1127 |     183 |         45 |           70.3 |
| val     |     95 |      18 |         10 |           15.6 |
| test    |    105 |      18 |          9 |           14.1 |

Patients appearing in more than one split: **0** (asserted zero in code).
