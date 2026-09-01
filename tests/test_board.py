"""The board must be generated from the real engine, and must tell the story."""
from __future__ import annotations

import json
import re

from vigil.board import build_payload, render


class TestPayload:
    def test_the_room_is_congested(self):
        """An empty waiting room has no sequencing problem, so every ordering
        agrees and the board demonstrates nothing."""
        p = build_payload()
        waiting = [f["waiting"] for f in p["frames"]]
        assert max(waiting) >= 15

    def test_both_orderings_are_present_at_every_frame(self):
        for f in build_payload()["frames"]:
            assert "vigil" in f and "fifo" in f
            assert {r["id"] for r in f["vigil"]} == {r["id"] for r in f["fifo"]}, (
                "the two orderings must contain the same patients - only the order differs"
            )

    def test_the_hero_patient_climbs_as_she_deteriorates(self):
        """P17 arrives level 3 looking well and reaches level 1. If this stops
        happening the demo is silently broken."""
        p = build_payload()
        seen = []
        for f in p["frames"]:
            for n, r in enumerate(f["vigil"]):
                if r["id"] == "P17":
                    seen.append((f["minute"], n + 1, r["level"], r["deteriorating"]))
        assert seen, "P17 never appears on the board"
        assert seen[0][2] == 3, "P17 must arrive at level 3"
        assert any(d for _, _, _, d in seen), "P17 must be flagged as deteriorating"
        assert min(lv for _, _, lv, _ in seen) == 1, "P17 must reach level 1"
        first, best = seen[0][1], min(rank for _, rank, _, _ in seen)
        assert best < first, "P17 must climb the queue"

    def test_every_row_carries_its_rank_under_the_other_ordering(self):
        """Otherwise the toggle silently re-sorts and the effect is invisible."""
        for f in build_payload()["frames"]:
            for r in f["vigil"]:
                assert "otherRank" in r

    def test_level_one_is_marked_immediate(self):
        p = build_payload()
        for f in p["frames"]:
            for r in f["vigil"]:
                assert r["immediate"] == (r["level"] == 1)

    def test_patient_cards_carry_the_full_reasoning(self):
        for card in build_payload()["patients"].values():
            assert card["rationale"], "a card without reasoning is an alarm, not decision support"
            assert card["confidenceFactors"], "no score without a confidence indicator"


class TestRender:
    def test_it_writes_a_self_contained_page(self, tmp_path):
        out = render(tmp_path / "index.html")
        html = out.read_text(encoding="utf-8")
        assert html.startswith("<!doctype html>")
        assert "__PAYLOAD__" not in html, "payload not substituted"
        assert "src=" not in html.split("<script>")[0], "must have no external dependencies"

    def test_the_embedded_payload_parses(self, tmp_path):
        html = render(tmp_path / "index.html").read_text(encoding="utf-8")
        d = json.loads(re.search(r"const D = (\{.*?\});\n", html, re.S).group(1))
        assert d["frames"] and d["patients"]

    def test_it_states_that_the_patients_are_synthetic(self, tmp_path):
        html = render(tmp_path / "index.html").read_text(encoding="utf-8")
        assert "Synthetic patients" in html
        assert "not a medical device" in html
