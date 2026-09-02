"""One command that demonstrates the whole prototype.

    python -m vigil.demo              # everything, in order
    python -m vigil.demo --rubric     # the brief's checklist, with live evidence
    python -m vigil.demo --watch      # the waiting-room deterioration story
    python -m vigil.demo --experiment # FIFO vs Vigil, same shift, one change

Written to be run by someone who has never seen the repository. Every number it
prints is computed live from the engine at run time - nothing here is a stored
result or a transcript, so if the code regresses, the demo says so.
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta

from vigil.audit import AuditLog, record_assessment, record_override
from vigil.flow import WaitingRoom, profile
from vigil.flow.policy import RURAL_DISTRICT, URBAN_TRAUMA_CENTRE
from vigil.flow.room import EventKind
from vigil.sim import (SCENARIOS, by_id, compare, composition, coverage,
                       generate_surge, run_shift, surge_summary)
from vigil.sim.scenarios import T0
from vigil.triage import assess
from vigil.triage.confidence import ConfidenceLevel

W = 78


def _h(title: str) -> None:
    print(f"\n{'=' * W}\n  {title}\n{'=' * W}")


def _sub(title: str) -> None:
    print(f"\n-- {title} " + "-" * max(0, W - len(title) - 4))


def _ok(passed: bool) -> str:
    return "[PASS]" if passed else "[FAIL]"


# ── 1. every patient scored ───────────────────────────────────────────────────
def show_patients() -> None:
    _h("TRIAGE SCORING - 20 SYNTHETIC PATIENTS")
    c = composition()
    print(f"  {c['patients']} patients | {c['with_prior_record_pct']}% with a prior record "
          f"| {c['paediatric_pct']}% paediatric | {c['arrived_by_ambulance_pct']}% by ambulance")
    print(f"\n  {'ID':<5}{'AGE':<6}{'BAND':<12}{'INSTR':<8}{'SCORE':<7}"
          f"{'NURSE':<7}{'VIGIL':<7}{'CONF':<10}{'FLAGS'}")
    print("  " + "-" * (W - 2))
    for s in SCENARIOS:
        a = assess(s.snapshot)
        age = "?" if s.snapshot.age_years is None else f"{s.snapshot.age_years:g}"
        instr = "PEWS" if "PEWS" in a.score.instrument else "NEWS2"
        mark = " ^" if a.escalated else ("  " if a.nurse_acuity else "  ")
        print(f"  {s.id:<5}{age:<6}{a.age_band.value:<12}{instr:<8}{a.score.total:<7}"
              f"{str(a.nurse_acuity):<7}{str(a.recommended_level) + mark:<7}"
              f"{a.confidence.level.value:<10}{len(a.red_flags)}")
    print("\n  ^ = escalated above the nurse's level. The system never lowers one.")


# ── 2. the required edge cases, individually ──────────────────────────────────
def show_edge_cases() -> None:
    _h("THE CASES THE BRIEF ASKS FOR, ONE AT A TIME")
    for pid, why in [
        ("P22", "PAEDIATRIC - compensated shock, the case an adult model calls stable"),
        ("P08", "PAEDIATRIC CONTROL - same shape, different age, must NOT escalate"),
        ("P31", "ZERO-HISTORY - no age, no vitals, no record"),
        ("P04", "AMBIGUOUS - atypical ACS in a diabetic woman, no chest pain"),
        ("P02", "GERIATRIC - occult sepsis, afebrile"),
    ]:
        s = by_id(pid)
        a = assess(s.snapshot)
        _sub(f"{pid}  {why}")
        print(f"  complaint : {s.snapshot.chief_complaint or '(none given)'}")
        print(f"  vitals    : HR {s.snapshot.hr} RR {s.snapshot.rr} SpO2 {s.snapshot.spo2} "
              f"SBP {s.snapshot.sbp} T {s.snapshot.temp_c}")
        print(f"  instrument: {a.score.instrument} = {a.score.total}")
        print(f"  nurse {a.nurse_acuity}  ->  VIGIL {a.recommended_level}"
              f"{'  ESCALATED' if a.escalated else '  (deferred to nurse)'}")
        print(f"  confidence: {a.confidence.level.value} ({a.confidence.score:.2f})")
        if a.confidence.next_best_action:
            print(f"  next step : {a.confidence.next_best_action}")
        for f in a.red_flags:
            print(f"  RED FLAG  : {f.name}")
            print(f"              why missed: {f.why_missed}")


# ── 3. the waiting-room story ─────────────────────────────────────────────────
def show_watch() -> None:
    _h("WATCH - DETERIORATION WITH EVERY READING STILL NORMAL")
    s = by_id("P17")
    room = WaitingRoom(policy=URBAN_TRAUMA_CENTRE)
    wp = room.admit(s.snapshot, now=T0)
    a = wp.assessment
    print(f"  P17, 68F, abdominal pain, takes a beta-blocker.")
    print(f"  T+0   HR {s.snapshot.hr} RR {s.snapshot.rr} SpO2 {s.snapshot.spo2} "
          f"SBP {s.snapshot.sbp}   -> NEWS2 {a.score.total}, level {a.recommended_level}, "
          f"re-check {a.monitoring.interval_minutes} min")
    print("        Every value is inside its normal range. The nurse is right.")

    for offset, vitals in s.follow_up:
        wp, evs = room.record_observation("P17", at=T0 + timedelta(minutes=offset), **vitals)
        v = " ".join(f"{k.upper()} {val}" for k, val in vitals.items())
        print(f"\n  T+{offset:<3} {v}")
        print(f"        -> NEWS2 {wp.assessment.score.total}, "
              f"re-check {wp.assessment.monitoring.interval_minutes} min")
        for e in evs:
            if e.kind is EventKind.DETERIORATION:
                print(f"        !! {e.detail}")
                print(f"           action: {e.action}")
    print(f"\n  Two readings forty minutes apart are the entire signal.")
    print(f"  No telemetry, no monitor per chair - a shared kiosk and a re-check clock.")


# ── 4. surge ──────────────────────────────────────────────────────────────────
def show_surge() -> None:
    _h("SURGE - 3x NORMAL VOLUME")
    arrivals = generate_surge(hours=3.0, multiplier=3.0, seed=42)
    s = surge_summary(arrivals)
    print(f"  {s['arrivals']} arrivals over {s['hours']}h = {s['arrivals_per_hour']}/hour "
          f"(normal is ~21/hour for a 500-visit department)")
    print(f"  {s['triage_degraded_by_crowding']} arrivals ({s['triage_degraded_pct']}%) had "
          f"their triage level degraded by crowding -")
    print(f"  the documented effect where a full department under-triages hardest.")

    res = run_shift(arrivals, ordering="vigil", minutes_per_patient=4.0, shift_minutes=180)
    kinds: dict[str, int] = {}
    for e in res.events:
        kinds[e.kind.value] = kinds.get(e.kind.value, 0) + 1
    _sub("what the system did under load")
    for k, n in sorted(kinds.items(), key=lambda x: -x[1]):
        print(f"  {n:>5}  {k}")
    cap = [e for e in res.events if e.kind is EventKind.CAPACITY]
    if cap:
        print(f"\n  CAPACITY: {cap[0].detail}")
        print(f"            action: {cap[0].action}")
        print("  The schedule is escalated as a staffing problem rather than")
        print("  accumulating silently as overdue tasks.")


# ── 5. the counterfactual ─────────────────────────────────────────────────────
def show_experiment() -> None:
    _h("EXPERIMENT - SAME SHIFT, ONE CHANGE")
    print("  Identical arrivals, identical deteriorations, identical capacity.")
    print("  Only the queue ordering differs.\n")
    r = compare()
    p = r["paired_on_deteriorating_patients"]
    f, v = r["fifo"], r["vigil"]
    print(f"  {'':<44}{'FIFO':>10}{'VIGIL':>10}")
    print("  " + "-" * (W - 2))
    print(f"  {'patients seen (capacity held constant)':<44}{f['seen']:>10}{v['seen']:>10}")
    print(f"  {'MEAN wait, all patients (min)':<44}{f['mean_wait_min']:>10}{v['mean_wait_min']:>10}")
    print(f"  {'median wait (min)':<44}{f['median_wait_min']:>10}{v['median_wait_min']:>10}")
    print(f"  {'90th percentile wait (min)':<44}{f['p90_wait_min']:>10}{v['p90_wait_min']:>10}")
    print(f"  {'median deterioration -> seen (min)':<44}"
          f"{p['median_minutes_deterioration_to_seen_fifo']:>10}"
          f"{p['median_minutes_deterioration_to_seen_vigil']:>10}")
    print("\n  The MEAN wait is unchanged, and it has to be: re-ordering cannot create")
    print("  capacity. Total waiting time is conserved and only REDISTRIBUTED - the")
    print("  median falls sharply while the 90th percentile rises. Most patients wait")
    print("  less; a minority wait longer. That is the trade, and the anti-starvation")
    print("  property is what stops the tail growing without bound.")
    print(f"\n  Paired on the {p['n_matched']} patients who deteriorated in BOTH arms:")
    print(f"    median change      {p['median_change_min']:+} min")
    print(f"    reached sooner     {p['reached_sooner']}")
    print(f"    reached later      {p['reached_later']}   <- re-ordering is zero-sum "
          f"under fixed capacity")
    print(f"    unchanged          {p['unchanged']}")
    print(f"\n  {r['assumptions']['note']}")


# ── 6. override and audit ─────────────────────────────────────────────────────
def show_audit() -> None:
    _h("CLINICIAN OVERRIDE AND THE AUDIT TRAIL")
    log = AuditLog()
    room = WaitingRoom(policy=URBAN_TRAUMA_CENTRE)
    s = by_id("P04")                       # atypical ACS: Vigil escalates 4 -> 2
    wp = room.admit(s.snapshot, now=T0)
    record_assessment(log, wp.assessment)
    a = wp.assessment
    print(f"  P04: nurse said level {a.nurse_acuity}, Vigil recommends {a.recommended_level}.")
    print(f"       reason: {a.red_flags[0].name if a.red_flags else a.headline}")

    _sub("the clinician disagrees, and wins")
    ev = room.override("P04", new_level=4, by="Dr.Iyer",
                       reason="known reflux, identical presentation last month, ECG normal today",
                       at=T0 + timedelta(minutes=3))
    record_override(log, patient_id="P04", clinician="Dr.Iyer", was=a.recommended_level,
                    now_level=4, reason="known reflux, ECG normal today",
                    recommendation_rationale=a.rationale, at=T0 + timedelta(minutes=3))
    print(f"  {ev.detail}")
    print(f"  effective level is now {room.patients['P04'].effective_level} - "
          f"the clinician's decision stands.")

    _sub("what the system logged")
    for e in log:
        print(f"  [{e.index}] {e.action:<11} by {e.actor:<22} hash {e.entry_hash[:12]}...")
        if e.action == "override":
            print(f"      from level {e.payload['from_level']} to {e.payload['to_level']} "
                  f"({e.payload['direction']})")
            print(f"      reason: {e.payload['reason']}")
            print(f"      kept {len(e.payload['superseded_rationale'])} lines of the "
                  f"reasoning it overruled")
    ok, msg = log.verify()
    print(f"\n  chain check: {_ok(ok)} {msg}")

    _sub("tamper detection")
    from vigil.audit.log import AuditEntry
    log.entries[0] = AuditEntry(**{**log.entries[0].to_dict(),
                                   "payload": {"recommended_level": 5}})
    ok, msg = log.verify()
    print(f"  after altering entry 0: {_ok(not ok)} detected -> {msg}")
    print("  Tamper-EVIDENT, not tamper-proof: a full rewrite would still verify.")


# ── 7. site profiles ──────────────────────────────────────────────────────────
def show_profiles() -> None:
    _h("SCALABILITY - THE SAME ENGINE, TWO VERY DIFFERENT DEPARTMENTS")
    print(f"  {'':<26}{'URBAN (500/day)':>18}{'RURAL (120/day)':>18}")
    print("  " + "-" * (W - 2))
    for lvl in (1, 2, 3, 4, 5):
        print(f"  {'level ' + str(lvl) + ' target (min)':<26}"
              f"{URBAN_TRAUMA_CENTRE.target_minutes[lvl]:>18g}"
              f"{RURAL_DISTRICT.target_minutes[lvl]:>18g}")
    print(f"  {'convexity':<26}{URBAN_TRAUMA_CENTRE.convexity:>18}{RURAL_DISTRICT.convexity:>18}")
    print("\n  The rural profile holds the sickest to a TIGHTER target (retrieval takes")
    print("  longer) and relaxes low-acuity targets to something the department can")
    print("  actually meet. A target nobody can hit is not a safety standard - it is an")
    print("  alarm generator, and it is how these systems lose staff trust.")
    print("\n  No model is retrained. Only the declared policy changes.")


# ── the rubric ────────────────────────────────────────────────────────────────
def show_rubric() -> int:
    """The brief's Minimum Prototype Expectations, each with live evidence."""
    _h("ROUND 2 MINIMUM PROTOTYPE EXPECTATIONS - EVIDENCE")
    checks: list[tuple[bool, str, str]] = []

    n = len(SCENARIOS)
    checks.append((n >= 15, f"Triage scoring on at least 15-20 patient records",
                   f"{n} synthetic patients, all scored - see --patients"))

    cov = coverage()
    amb, paed, ger, zero = (cov["ambiguous presentation"], cov["paediatric"],
                            cov["geriatric"], cov["zero-history (first-time)"])
    checks.append((bool(amb and (paed or ger) and zero),
                   "At least one ambiguous, one paediatric/geriatric, one zero-history",
                   f"ambiguous {len(amb)} ({', '.join(amb[:3])}) | "
                   f"paediatric {len(paed)} ({', '.join(paed)}) | "
                   f"geriatric {len(ger)} ({', '.join(ger[:3])}) | "
                   f"zero-history {len(zero)}"))

    surge = surge_summary(generate_surge(hours=3.0, multiplier=3.0))
    checks.append((surge["arrivals_per_hour"] > 2.5 * 21,
                   "Behaviour under a simulated surge (3x normal volume)",
                   f"{surge['arrivals_per_hour']}/hour vs ~21 normal; "
                   f"{surge['triage_degraded_pct']}% triage degraded by crowding - see --surge"))

    all_have_conf = all(assess(s.snapshot).confidence.factors for s in SCENARIOS)
    abstains = [s.id for s in SCENARIOS
                if assess(s.snapshot).confidence.level is ConfidenceLevel.ABSTAIN]
    checks.append((all_have_conf,
                   "Uncertainty surfaced explicitly - no score without a confidence indicator",
                   f"every assessment carries 4 decomposed factors; "
                   f"{len(abstains)} abstain outright ({', '.join(abstains) or 'none'})"))

    log = AuditLog()
    a = assess(by_id("P04").snapshot)
    record_assessment(log, a)
    record_override(log, patient_id="P04", clinician="Dr.Iyer", was=a.recommended_level,
                    now_level=4, reason="known reflux, ECG normal today",
                    recommendation_rationale=a.rationale)
    ok, _ = log.verify()
    checks.append((ok and len(log.overrides()) == 1,
                   "At least one clinician override, and what the system logs",
                   f"hash-chained log, {len(log)} entries, verify() passes; "
                   f"override keeps both the decision and the reasoning it overruled - see --audit"))

    for passed, req, ev in checks:
        print(f"\n  {_ok(passed)} {req}")
        print(f"         {ev}")

    _sub("also required, from the brief's 'complexities' section")
    extra = [
        ("Age-banded thresholds, not a single adult model",
         "NEWS2 for adults, PEWS-style over PALS ranges for children; "
         "tests/test_safety_property.py::TestAgeBandedSafety"),
        ("Bias toward escalation under uncertainty, demonstrated explicitly",
         "recommended_level <= nurse_acuity swept over ~600 synthetic patients; "
         "tests/test_safety_property.py::TestSafetyMonotone"),
        ("Waiting-queue monitoring with both mandated triggers",
         "overdue re-check clock AND worsening re-recorded vitals - see --watch"),
        ("Reviewable, overridable, with an audit trail",
         "clinician override always wins; tamper-evident chain - see --audit"),
        ("Scalability across very different hospitals",
         "two site profiles, no retraining - see --profiles"),
        ("Stated regulatory jurisdiction",
         "India: DPDP Act 2023 + ABDM. See src/vigil/audit/log.py"),
    ]
    for req, ev in extra:
        print(f"\n  [DONE] {req}")
        print(f"         {ev}")

    passed = sum(1 for p, _, _ in checks if p)
    print(f"\n{'=' * W}\n  {passed}/{len(checks)} minimum expectations met, "
          f"verified live at run time.\n{'=' * W}")
    return 0 if passed == len(checks) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="vigil.demo", description="Vigil prototype demonstration")
    for flag, help_ in [
        ("rubric", "the brief's checklist with live evidence"),
        ("patients", "all 20 patients scored"),
        ("cases", "the required edge cases, one at a time"),
        ("watch", "waiting-room deterioration"),
        ("surge", "3x volume"),
        ("experiment", "FIFO vs Vigil counterfactual"),
        ("audit", "clinician override and the audit chain"),
        ("profiles", "two hospital site profiles"),
    ]:
        ap.add_argument(f"--{flag}", action="store_true", help=help_)
    args = ap.parse_args(argv)

    chosen = {k: v for k, v in vars(args).items() if v}
    if not chosen:
        print("\n" + "=" * W)
        print("  VIGIL - a triage assistant that never stops watching")
        print("  Accenture Innovation Challenge 2026 | PatientTriage.ai | Team Vigil")
        print("=" * W)
        show_patients(); show_edge_cases(); show_watch()
        show_surge(); show_experiment(); show_audit(); show_profiles()
        return show_rubric()

    if args.rubric:     return show_rubric()
    if args.patients:   show_patients()
    if args.cases:      show_edge_cases()
    if args.watch:      show_watch()
    if args.surge:      show_surge()
    if args.experiment: show_experiment()
    if args.audit:      show_audit()
    if args.profiles:   show_profiles()
    return 0


if __name__ == "__main__":
    sys.exit(main())
