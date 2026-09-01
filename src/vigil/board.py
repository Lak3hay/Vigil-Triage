"""Renders the live ED board as a single self-contained HTML file.

Two reasons this is a static file rather than a server:

* **A judge can open it.** No install, no Python, no API key - the published
  page at ``docs/index.html`` runs from GitHub Pages in a browser. A prototype
  that only runs on the author's machine has not been demonstrated.
* **It cannot fake anything.** Every number in the page is computed by the real
  engine at generation time and baked into the payload. There is no separate
  demo data path, so the board cannot drift from the code the tests cover.

The page replays one shift. The control that matters is the ordering toggle:
the same patients, the same minute, re-ranked under the status quo and under
Vigil - which is the counterfactual from ``vigil.sim.runner`` made visible.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from vigil.flow import WaitingRoom
from vigil.flow.policy import URBAN_TRAUMA_CENTRE, HarmPolicy
from vigil.flow.room import EventKind, WaitingPatient
from vigil.sim import SCENARIOS
from vigil.sim.scenarios import T0, Scenario
from vigil.triage.confidence import ConfidenceLevel

TICK = 5          # minutes between frames
SHIFT = 220       # minutes of shift to replay
SERVICE = 7       # one clinician slot every N minutes

# The board must be CONGESTED or it demonstrates nothing: in an empty waiting
# room every ordering agrees, and a patient is seen before they have time to
# deteriorate. So the named cohort is interleaved with surge arrivals to build a
# realistic queue -- arrivals outpace service, which is the condition the whole
# system exists for.
FILLER = 20


def _patient_card(wp: WaitingPatient, scen: Scenario | None) -> dict:
    """Static facts about one patient - emitted once, not per frame."""
    a, s = wp.assessment, wp.snapshot
    return {
        "id": s.patient_id,
        "age": None if s.age_years is None else round(s.age_years, 2),
        "sex": s.sex or "",
        "band": a.age_band.value,
        "complaint": s.chief_complaint or "(none given)",
        "arrival": s.arrival_mode or "",
        "hasRecord": s.has_prior_record,
        "instrument": a.score.instrument,
        "score": a.score.total,
        "scoreParts": dict(a.score.components),
        "scoreMissing": list(a.score.missing),
        "nurse": a.nurse_acuity,
        "computed": a.computed_level,
        "vigil": a.recommended_level,
        "escalated": a.escalated,
        "confidence": a.confidence.level.value,
        "confidenceScore": a.confidence.score,
        "confidenceFactors": [
            {"name": f.name, "score": f.score, "detail": f.detail, "remedy": f.remedy}
            for f in a.confidence.factors
        ],
        "nextAction": a.confidence.next_best_action,
        "flags": [{"name": f.name, "why": f.why_missed, "rationale": f.rationale}
                  for f in a.red_flags],
        "rationale": list(a.rationale),
        "recheck": a.monitoring.interval_minutes,
        "recheckWhy": a.monitoring.rationale,
        "stream": wp.routing().stream,
        "streamWhy": wp.routing().rationale,
        "whyIncluded": scen.why_included if scen else "",
        "decisions": [{"authority": d.authority, "action": d.action, "detail": d.detail}
                      for d in a.decisions],
    }


def build_payload(policy: HarmPolicy = URBAN_TRAUMA_CENTRE) -> dict:
    """Replay a shift and capture the board at every tick, under both orderings."""
    from vigil.sim.surge import generate_surge

    named = [(4.0 + i * 6.0, s) for i, s in enumerate(SCENARIOS)]
    filler = [(m * 0.9, s) for m, s in generate_surge(hours=2.2, multiplier=3.0, seed=7)[:FILLER]]
    arrivals = sorted(named + filler, key=lambda a: a[0])
    by_id = {s.id: s for _, s in arrivals}

    room = WaitingRoom(policy=policy)
    pending = list(arrivals)
    follow_ups: list[tuple[float, str, dict]] = []
    cards: dict[str, dict] = {}
    frames: list[dict] = []
    feed: list[dict] = []
    next_slot = 0.0
    seen_at: dict[str, float] = {}

    t = 0.0
    while t <= SHIFT:
        now = T0 + timedelta(minutes=t)
        new_events: list[dict] = []

        while pending and pending[0][0] <= t:
            minute, scen = pending.pop(0)
            wp = room.admit(scen.snapshot, now=now)
            cards[scen.id] = _patient_card(wp, by_id.get(scen.id))
            for offset, vitals in scen.follow_up:
                follow_ups.append((minute + offset, scen.id, vitals))
            for e in room.events[-3:]:
                if e.patient_id == scen.id:
                    new_events.append({**e.to_dict(), "minute": t})

        for f in [f for f in follow_ups if f[0] <= t]:
            follow_ups.remove(f)
            _, pid, vitals = f
            if pid not in room.patients or not room.patients[pid].is_waiting:
                continue
            wp, evs = room.record_observation(pid, at=now, **vitals)
            cards[pid] = {**cards[pid], **_patient_card(wp, by_id.get(pid)),
                          "vitalsNote": ", ".join(f"{k.upper()} {v}" for k, v in vitals.items())}
            for e in evs:
                new_events.append({**e.to_dict(), "minute": t})

        for e in room.tick(now):
            new_events.append({**e.to_dict(), "minute": t})

        while next_slot <= t:
            waiting = room.waiting()
            if not waiting:
                break
            nxt = room.ranked(now)[0][0]
            room.mark_seen(nxt.patient_id, at=now)
            seen_at[nxt.patient_id] = t
            new_events.append({"at": now.isoformat(), "kind": "seen_by_clinician",
                               "patient_id": nxt.patient_id, "minute": t,
                               "detail": f"seen after {nxt.waited_minutes(now):.0f} min",
                               "severity": "info", "action": ""})
            next_slot += SERVICE

        waiting = room.waiting()
        ranked = room.ranked(now)
        fifo = sorted(waiting, key=lambda p: (p.effective_level, p.arrived_at))

        def _row(wp: WaitingPatient, cost: float) -> dict:
            return {
                "id": wp.patient_id,
                "immediate": wp.effective_level == 1,
                "level": wp.effective_level,
                "cost": round(cost, 1),
                "waited": round(wp.waited_minutes(now)),
                "toBreach": policy.minutes_to_breach(wp.effective_level, wp.waited_minutes(now)),
                "deteriorating": wp.deteriorating,
                "overdue": wp.is_overdue(now),
                "flags": len(wp.assessment.red_flags),
                "abstain": wp.assessment.confidence.level is ConfidenceLevel.ABSTAIN,
                "why": policy.explain(wp.effective_level, wp.waited_minutes(now),
                                      deteriorating=wp.deteriorating,
                                      unresolved_flag=wp.has_unresolved_flag),
            }

        vigil_rows = [_row(wp, c) for wp, c in ranked]
        fifo_rows = [_row(wp, wp.cost_of_waiting(policy, now)) for wp in fifo]
        # Cross-reference the two orderings so the board can show, per patient,
        # how many places they move when the policy changes. Without this the
        # toggle silently re-sorts and the difference is invisible.
        v_pos = {r["id"]: n for n, r in enumerate(vigil_rows)}
        f_pos = {r["id"]: n for n, r in enumerate(fifo_rows)}
        for r in vigil_rows:
            r["otherRank"] = f_pos.get(r["id"])
        for r in fifo_rows:
            r["otherRank"] = v_pos.get(r["id"])

        feed.extend(new_events)
        frames.append({
            "minute": round(t),
            "clock": (T0 + timedelta(minutes=t)).strftime("%H:%M"),
            "waiting": len(waiting),
            "vigil": vigil_rows,
            "fifo": fifo_rows,
            "events": new_events,
        })
        t += TICK

    return {
        "site": policy.name,
        "tick": TICK,
        "targets": {str(k): v for k, v in policy.target_minutes.items()},
        "patients": cards,
        "frames": frames,
        "feed": feed,
        "seenAt": seen_at,
    }


def render(out: str | Path = "docs/index.html", policy: HarmPolicy = URBAN_TRAUMA_CENTRE) -> Path:
    payload = build_payload(policy)
    html = _TEMPLATE.replace("__PAYLOAD__", json.dumps(payload, separators=(",", ":")))
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")
    return p


_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vigil - live ED board</title>
<style>
:root{
  --deep:#460073; --purple:#A100FF; --mid:#7500C0; --lav:#F3ECFE; --lavE:#E0CCFB;
  --ink:#14121A; --grey:#5A5A66; --line:#E4E2EC; --bg:#F7F6FA; --card:#fff;
  --red:#C8102E; --redT:#FDECEF; --amber:#B46A00; --amberT:#FFF4E3;
  --green:#0F7B55; --greenT:#E9F6F1;
}
*{box-sizing:border-box} html,body{margin:0;padding:0}
body{font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;
     background:var(--bg); color:var(--ink)}
header{background:var(--deep); color:#fff; padding:12px 20px; display:flex;
       align-items:center; gap:18px; flex-wrap:wrap}
header h1{font-size:19px; margin:0; letter-spacing:.5px}
header .sub{color:#C9A6F5; font-size:13px}
header .spacer{flex:1}
.clock{font-size:22px; font-weight:700; font-variant-numeric:tabular-nums}
.wrap{display:grid; grid-template-columns:1fr 340px; gap:14px; padding:14px;
      max-width:1500px; margin:0 auto}
@media(max-width:1000px){.wrap{grid-template-columns:1fr}}
.panel{background:var(--card); border:1px solid var(--line); border-radius:10px; overflow:hidden}
.panel h2{font-size:12px; letter-spacing:.9px; text-transform:uppercase; color:var(--grey);
          margin:0; padding:11px 14px; border-bottom:1px solid var(--line); background:#FCFBFE}
.controls{display:flex; gap:10px; align-items:center; padding:11px 14px;
          border-bottom:1px solid var(--line); flex-wrap:wrap; background:#FCFBFE}
button{font:inherit; border:1px solid var(--lavE); background:var(--lav); color:var(--deep);
       padding:6px 13px; border-radius:6px; cursor:pointer; font-weight:600}
button:hover{background:var(--lavE)}
button.on{background:var(--deep); color:#fff; border-color:var(--deep)}
input[type=range]{flex:1; min-width:150px; accent-color:var(--purple)}
.toggle{display:inline-flex; border:1px solid var(--lavE); border-radius:6px; overflow:hidden}
.toggle button{border:0; border-radius:0}
table{width:100%; border-collapse:collapse}
th{font-size:10.5px; letter-spacing:.8px; text-transform:uppercase; color:var(--grey);
   text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); font-weight:600}
td{padding:9px 10px; border-bottom:1px solid #F1F0F5; vertical-align:middle}
tr.row{cursor:pointer} tr.row:hover{background:var(--lav)}
tr.sel{background:var(--lav); box-shadow:inset 3px 0 0 var(--purple)}
tr.det{background:var(--amberT)} tr.det:hover{background:#FDEBCF}
.pos{font-weight:700; color:var(--grey); width:28px}
.pid{font-weight:700}
.lvl{display:inline-block; width:22px; text-align:center; color:#fff; border-radius:4px;
     font-weight:700; font-size:12px; padding:2px 0}
.l1{background:var(--red)} .l2{background:#E2571F} .l3{background:var(--amber)}
.l4{background:#7A9A2E} .l5{background:var(--green)}
.bar{height:7px; background:#EBEAF0; border-radius:4px; overflow:hidden; min-width:70px}
.bar i{display:block; height:100%; border-radius:4px; background:var(--green)}
.bar.a i{background:var(--amber)} .bar.r i{background:var(--red)}
.tag{font-size:10.5px; font-weight:700; padding:2px 6px; border-radius:4px; margin-left:4px;
     white-space:nowrap}
.t-det{background:var(--amber); color:#fff} .t-od{background:var(--red); color:#fff}
.t-ab{background:var(--grey); color:#fff} .t-fl{background:var(--redT); color:var(--red)}
.t-up{background:var(--green); color:#fff} .t-dn{background:#8A8A96; color:#fff}
.feed{max-height:330px; overflow-y:auto}
.ev{padding:8px 13px; border-bottom:1px solid #F1F0F5; font-size:12.5px}
.ev b{font-variant-numeric:tabular-nums; color:var(--grey); font-weight:600}
.ev.urgent{background:var(--redT); border-left:3px solid var(--red)}
.ev.attention{background:var(--amberT); border-left:3px solid var(--amber)}
.ev .act{color:var(--mid); font-weight:600}
.detail{padding:13px 14px; font-size:13px}
.detail h3{margin:0 0 3px; font-size:16px}
.detail .meta{color:var(--grey); margin-bottom:11px}
.kv{display:grid; grid-template-columns:auto 1fr; gap:3px 10px; margin:9px 0}
.kv dt{color:var(--grey)} .kv dd{margin:0; font-weight:600}
.flag{background:var(--redT); border-left:3px solid var(--red); padding:8px 10px;
      border-radius:0 6px 6px 0; margin:7px 0}
.flag b{color:var(--red)} .flag .why{color:var(--grey); font-style:italic; font-size:12px}
.conf{margin:4px 0} .conf .f{display:flex; align-items:center; gap:7px; margin:3px 0;
      font-size:12.5px}
.conf .f span:first-child{width:105px; color:var(--grey)}
.rat{margin:9px 0 0; padding-left:17px; font-size:12.5px; color:#38343F}
.rat li{margin:3px 0}
.note{background:var(--lav); border-radius:6px; padding:9px 11px; font-size:12.5px;
      color:var(--deep); margin:9px 0}
.foot{max-width:1500px; margin:0 auto; padding:6px 20px 26px; color:var(--grey); font-size:12px}
.stat{display:flex; gap:20px; padding:10px 14px; border-bottom:1px solid var(--line);
      background:#FCFBFE; font-size:12.5px; flex-wrap:wrap}
.stat b{font-size:17px; display:block; color:var(--deep); font-variant-numeric:tabular-nums}
.stat span{color:var(--grey)}
</style></head><body>
<header>
  <h1>VIGIL</h1>
  <div class="sub">Emergency Department &middot; waiting room &middot; <span id="site"></span></div>
  <div class="spacer"></div>
  <div class="clock" id="clock">--:--</div>
</header>

<div class="wrap">
  <div>
    <div class="panel">
      <div class="controls">
        <button id="play">&#9654; Play</button>
        <input type="range" id="scrub" min="0" value="0">
        <span id="tlabel" style="font-variant-numeric:tabular-nums;color:var(--grey)"></span>
        <div class="toggle">
          <button id="bVigil" class="on">Vigil ordering</button>
          <button id="bFifo">Status quo (FIFO)</button>
        </div>
      </div>
      <div class="stat">
        <div><b id="sWait">0</b><span>waiting</span></div>
        <div><b id="sDet">0</b><span>deteriorating</span></div>
        <div><b id="sOver">0</b><span>re-check overdue</span></div>
        <div><b id="sBreach">0</b><span>past target</span></div>
      </div>
      <table>
        <thead><tr><th></th><th>Patient</th><th>Complaint</th><th>Lvl</th>
          <th>Waited</th><th>Cost of waiting</th><th></th></tr></thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
    <div class="panel" style="margin-top:14px">
      <h2>Assessment</h2><div class="detail" id="detail">Select a patient.</div>
    </div>
  </div>
  <div>
    <div class="panel"><h2>Event feed</h2><div class="feed" id="feed"></div></div>
    <div class="panel" style="margin-top:14px"><h2>How to read this</h2>
      <div class="detail" style="font-size:12.5px;color:#38343F">
        <p style="margin:0 0 8px"><b>Cost of waiting</b> is the second axis. Acuity asks how
        sick you are; this asks what waiting costs you, and it rises every minute.
        It is a <b>declared scheduling policy</b>, not an estimated causal quantity.</p>
        <p style="margin:0 0 8px">Because the curve is convex, a low-acuity patient
        eventually overtakes a higher one - <b>the queue cannot starve anyone</b>.
        Level&nbsp;1 is the deliberate exception and is never overtaken.</p>
        <p style="margin:0 0 8px">Toggle to <b>Status quo</b> to see the same patients at the
        same minute ordered by acuity then arrival, with nobody re-ranked after triage.</p>
        <p style="margin:0 0 8px">The <span class="tag t-up">&#9650;n</span> badge shows how
        many places a patient moves <i>because of</i> the policy you have selected, compared
        with the other ordering.</p>
        <p style="margin:0"><b>Watch P17</b> from minute 40. Every reading stays inside its
        normal range; the trajectory does not. She arrives level&nbsp;3 and reaches
        level&nbsp;1 by minute&nbsp;95.</p>
      </div>
    </div>
  </div>
</div>
<div class="foot">
  Synthetic patients. Deterministic engine - every number here is computed by the same code
  the tests cover. Prototype for the Accenture Innovation Challenge 2026; not a medical device.
</div>

<script>
const D = __PAYLOAD__;
let i = 0, mode = "vigil", sel = null, timer = null;
const $ = id => document.getElementById(id);
$("site").textContent = D.site;
$("scrub").max = D.frames.length - 1;

const esc = s => String(s).replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));

function drawRows(f){
  const rows = f[mode] || [];
  $("rows").innerHTML = rows.map((r, n) => {
    const p = D.patients[r.id] || {};
    // Level 1 sits on a separate priority scale, so scaling bars against it
    // would flatten everyone else to nothing. Immediate patients get a label.
    const queued = rows.filter(x => !x.immediate).map(x => x.cost);
    const max = Math.max(1, ...queued);
    const pct = r.immediate ? 100 : Math.min(100, 100 * r.cost / max);
    const cls = r.immediate ? "r" : r.cost > max * .55 ? "r" : r.cost > max * .22 ? "a" : "";
    const moved = r.otherRank == null ? 0 : r.otherRank - n;
    const delta = moved > 0
      ? `<span class="tag t-up">&#9650;${moved}</span>`
      : moved < 0 ? `<span class="tag t-dn">&#9660;${-moved}</span>` : "";
    const tags = [
      r.deteriorating ? '<span class="tag t-det">DETERIORATING</span>' : "",
      r.overdue ? '<span class="tag t-od">RE-CHECK DUE</span>' : "",
      r.abstain ? '<span class="tag t-ab">ABSTAINED</span>' : "",
      r.flags ? `<span class="tag t-fl">${r.flags} FLAG${r.flags>1?"S":""}</span>` : "",
    ].join("");
    const age = p.age == null ? "?" : (p.age < 1 ? Math.round(p.age*12)+"mo" : p.age);
    return `<tr class="row ${r.id===sel?"sel":""} ${r.deteriorating?"det":""}" data-id="${r.id}">
      <td class="pos">${n+1}</td>
      <td><span class="pid">${r.id}</span> <span style="color:var(--grey)">${age} ${esc(p.sex||"")}</span></td>
      <td>${esc(p.complaint||"")}</td>
      <td><span class="lvl l${r.level}">${r.level}</span></td>
      <td style="font-variant-numeric:tabular-nums">${r.waited}m</td>
      <td>${r.immediate
            ? '<span style="color:var(--red);font-weight:700;font-size:11.5px">IMMEDIATE</span>'
            : `<div class="bar ${cls}"><i style="width:${pct}%"></i></div>`}</td>
      <td>${delta}${tags}</td></tr>`;
  }).join("") || '<tr><td colspan="7" style="color:var(--grey);padding:18px">Waiting room empty.</td></tr>';
  document.querySelectorAll("tr.row").forEach(tr =>
    tr.onclick = () => { sel = tr.dataset.id; draw(); });
}

function drawDetail(f){
  const rows = f[mode] || [];
  if(!sel || !D.patients[sel]){ $("detail").innerHTML = "Select a patient."; return; }
  const p = D.patients[sel], r = rows.find(x => x.id === sel);
  const age = p.age == null ? "age not recorded" : (p.age < 1 ? Math.round(p.age*12)+" months" : p.age + "y");
  const conf = p.confidenceFactors.map(c => {
    const col = c.score < .6 ? "var(--red)" : c.score < .8 ? "var(--amber)" : "var(--green)";
    return `<div class="f"><span>${c.name.replace("_"," ")}</span>
      <div class="bar" style="width:56px"><i style="width:${Math.round(c.score*100)}%;background:${col}"></i></div>
      <span style="color:var(--grey);flex:1">${esc(c.detail)}</span></div>`;
  }).join("");
  $("detail").innerHTML = `
    <h3>${p.id} &middot; ${age} ${esc(p.sex)} &middot; ${esc(p.complaint)}</h3>
    <div class="meta">${esc(p.band)} &middot; ${p.hasRecord ? "prior record available" : "no prior record"}
      ${p.arrival ? "&middot; " + esc(p.arrival) : ""}</div>
    <dl class="kv">
      <dt>Instrument</dt><dd>${esc(p.instrument)} = ${p.score}</dd>
      <dt>Nurse level</dt><dd>${p.nurse ?? "-"}</dd>
      <dt>Vigil recommends</dt><dd>${p.vigil}${p.escalated ? " &nbsp;<span style='color:var(--red)'>ESCALATED</span>" : ""}</dd>
      <dt>Confidence</dt><dd>${p.confidence} (${p.confidenceScore})</dd>
      <dt>Re-check within</dt><dd>${p.recheck} min</dd>
      <dt>Stream</dt><dd>${esc(p.stream)}</dd>
      ${r ? `<dt>Queue position</dt><dd>${esc(r.why)}</dd>` : ""}
    </dl>
    ${p.flags.map(f => `<div class="flag"><b>${esc(f.name)}</b><br>${esc(f.rationale)}
        <div class="why">Why it gets missed: ${esc(f.why)}</div></div>`).join("")}
    <div class="conf"><b style="font-size:12px;color:var(--grey)">CONFIDENCE</b>${conf}</div>
    ${p.nextAction ? `<div class="note"><b>Most useful next step:</b> ${esc(p.nextAction)}</div>` : ""}
    <ul class="rat">${p.rationale.map(x => `<li>${esc(x)}</li>`).join("")}</ul>
    ${p.whyIncluded ? `<div class="note"><b>Why this patient is in the demo:</b> ${esc(p.whyIncluded)}</div>` : ""}`;
}

function drawFeed(){
  const upto = D.feed.filter(e => e.minute <= D.frames[i].minute).slice(-70).reverse();
  $("feed").innerHTML = upto.map(e => `<div class="ev ${e.severity}">
      <b>+${e.minute}m</b> &nbsp;<b>${e.patient_id}</b> ${esc(e.detail)}
      ${e.action ? `<div class="act">&rarr; ${esc(e.action)}</div>` : ""}</div>`).join("")
    || '<div class="ev" style="color:var(--grey)">No events yet.</div>';
}

function draw(){
  const f = D.frames[i];
  $("clock").textContent = f.clock;
  $("tlabel").textContent = "+" + f.minute + " min";
  $("scrub").value = i;
  const rows = f[mode] || [];
  $("sWait").textContent = rows.length;
  $("sDet").textContent = rows.filter(r => r.deteriorating).length;
  $("sOver").textContent = rows.filter(r => r.overdue).length;
  $("sBreach").textContent = rows.filter(r => r.toBreach < 0).length;
  drawRows(f); drawDetail(f); drawFeed();
}

$("scrub").oninput = e => { i = +e.target.value; draw(); };
$("play").onclick = () => {
  if(timer){ clearInterval(timer); timer = null; $("play").innerHTML = "&#9654; Play"; return; }
  $("play").innerHTML = "&#10074;&#10074; Pause";
  timer = setInterval(() => {
    i = (i + 1) % D.frames.length; draw();
    if(i === 0){ clearInterval(timer); timer = null; $("play").innerHTML = "&#9654; Play"; }
  }, 550);
};
$("bVigil").onclick = () => { mode="vigil"; $("bVigil").classList.add("on");
  $("bFifo").classList.remove("on"); draw(); };
$("bFifo").onclick = () => { mode="fifo"; $("bFifo").classList.add("on");
  $("bVigil").classList.remove("on"); draw(); };

sel = "P17"; draw();
</script></body></html>
"""


if __name__ == "__main__":
    print("wrote", render())
