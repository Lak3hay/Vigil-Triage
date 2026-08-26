"""Generate reports/cohort.md -- what the cohort is made of, and what was excluded.

    python scripts/cohort_report.py [--dataset mimic-iv-ed] [--root PATH]

Every number carries its configuration, because a number without its configuration
is not a result (PLAN.md 13).
"""
from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from vigil.data import get_dataset
from vigil.data.cohort import CohortSpec, build_cohort, characterise, missingness_by_channel
from vigil.data.landmarks import LandmarkSpec, build_landmarks
from vigil.data.splits import SplitSpec, apply_splits, make_splits, split_summary


def _md(df: pd.DataFrame, **kw) -> str:
    return df.to_markdown(**kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="mimic-iv-ed")
    ap.add_argument("--root", default="data/raw/mimic-iv-ed-demo")
    ap.add_argument("--out", default="reports/cohort.md")
    args = ap.parse_args()

    cohort_spec = CohortSpec()
    lm_spec = LandmarkSpec(grid_minutes=(30, 60, 90, 120, 180, 240, 360))
    split_spec = SplitSpec(strategy="patient_random", seed=42)

    ds = get_dataset(args.dataset, root=args.root)
    stays, vitals = ds.stays(), ds.vitals()
    cohort, crep = build_cohort(stays, vitals, cohort_spec)
    landmarks, lrep = build_landmarks(cohort, vitals, lm_spec)
    splits = make_splits(cohort, split_spec)
    joined = apply_splits(landmarks, splits)

    # Interim outcome: the only label expressible without the MIMIC-IV hospital module.
    outcome = stays.set_index("stay_id")["disposition"].map(
        lambda d: "admitted" if d in ("ADMITTED", "TRANSFER") else "not admitted"
    )
    n_slots = len(lm_spec.grid_minutes)
    slot_denom = outcome.reindex(cohort["stay_id"]).value_counts() * n_slots

    is_demo = "demo" in str(args.root)
    out = [
        "# Cohort report",
        "",
        f"*Generated {dt.date.today().isoformat()} · dataset `{args.dataset}` · root `{args.root}`*",
        "",
    ]
    if is_demo:
        out += [
            "> ⚠️ **Built on the MIMIC-IV-ED demo subset, which is not representative.**",
            "> It is 68% admitted with acuity skewed to levels 1-3; a real ED is neither.",
            "> This report demonstrates that the pipeline runs and that the audit machinery",
            "> works. **No prevalence, rate or performance figure here should be quoted.**",
            "",
        ]
    out += [
        "## Configuration",
        "",
        "```",
        f"CohortSpec   {asdict(cohort_spec)}",
        f"LandmarkSpec {asdict(lm_spec)}",
        f"SplitSpec    {asdict(split_spec)}",
        "```",
        "",
        "## 1. Exclusions",
        "",
        "```",
        str(crep),
        "```",
        "",
        "### Stratified by outcome",
        "",
        "The column to read is `disparity` -- the widest gap in removal rate between",
        "classes. A filter with a large disparity is selecting on the outcome and has",
        "stopped being a filter.",
        "",
        "**Interim outcome is admission vs. discharge**, the only label expressible from",
        "the ED module alone. ICU-within-24h and death-within-72h need the MIMIC-IV",
        "hospital and ICU modules (PLAN.md 3.1), and this audit should be re-run against",
        "them once available -- admission is a weak proxy and may hide a disparity that a",
        "clinical outcome would expose.",
        "",
        _md(crep.stratify(outcome), index=False),
        "",
        "## 2. Table 1",
        "",
        _md(characterise(cohort, vitals, by="disposition")),
        "",
        "## 3. Missingness by channel",
        "",
        "Two different questions: the share of *observations* lacking a channel, and the",
        "share of *stays* where it was never recorded at all. A channel measured once and",
        "then never again is present at the stay level and absent for most of the",
        "trajectory. Missingness is a feature here, not a nuisance (PLAN.md 5.1).",
        "",
        _md(missingness_by_channel(cohort, vitals), index=False),
        "",
        "## 4. Landmarks",
        "",
        "```",
        str(lrep),
        "```",
        "",
        "Landmark drops are counted per landmark but keyed by stay, so the audit needs an",
        "explicit denominator (eligible slots per class) rather than a per-stay one --",
        "otherwise the numerator counts landmarks and the denominator counts stays.",
        "",
        _md(lrep.stratify(outcome, denominator=slot_denom), index=False),
        "",
        "## 5. Splits",
        "",
        f"Strategy `{split_spec.strategy}`. "
        "**Not temporal** -- MIMIC shifts dates per patient, so `intime` cannot order",
        "patients and a split built on it would look temporal without being temporal.",
        "A true temporal split needs `patients.anchor_year_group` from the hospital",
        "module (PLAN.md 4). Any result computed on this split must say so.",
        "",
        _md(split_summary(joined)),
        "",
        f"Patients appearing in more than one split: "
        f"**{int((apply_splits(cohort, splits).groupby('patient_id')['split'].nunique() > 1).sum())}** "
        "(asserted zero in code).",
        "",
    ]

    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8")
    print(f"wrote {path}  ({len(cohort)} stays, {len(landmarks)} landmarks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
