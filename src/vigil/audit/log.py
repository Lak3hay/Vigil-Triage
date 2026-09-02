"""Tamper-evident audit log.

The Round 2 brief:

    "Clinical accountability and liability mean any recommendation must remain
    reviewable and overridable by a licensed clinician, with a clear audit
    trail and compliance with health-data regulation."

**Assumed jurisdiction: India - Digital Personal Data Protection Act 2023, with
ABDM/ABHA as the health-record layer.** Stated because the brief requires it,
and because it determines what follows: purpose limitation, data minimisation,
and a record of automated decisions that a person can inspect and contest.

## Why the chain

An ordinary log answers "what did the system recommend?". It does not answer
"has this record been altered since?" - and in a dispute about a missed
diagnosis, that second question is the one that matters. Each entry therefore
carries the hash of the entry before it, so the log is a chain: change any past
entry and every subsequent hash stops matching, and :meth:`AuditLog.verify`
says exactly where.

This is deliberately *tamper-evident*, not tamper-proof. Anyone who can rewrite
the whole file can recompute the whole chain. Making it tamper-proof needs an
external anchor - a witnessed digest, an append-only store, a signature from a
key the application cannot reach - and that is a deployment decision, not a
prototype one. Claiming more than evidence would be exactly the overreach this
project keeps refusing.

## What is deliberately not logged

Data minimisation is a legal obligation under DPDP, not a nicety, so the log
records the *reasoning* rather than the person: a pseudonymous patient
reference, the inputs that drove a decision, and the decision itself. Name,
contact details and identifiers stay in the hospital record system where they
already are and where consent already governs them.

**And a rule that is about fairness rather than security.** The disagreement
record - every time a clinician's level differs from the recommendation - exists
to improve the model and to audit the system. It must never be repurposed for
performance management of individual staff. A tool that nurses believe is
scoring them will be worked around within a week, whatever it does clinically,
so this is a design constraint and not only an ethical one. In deployment it
belongs in the contract, not merely in this docstring.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

#: Bumped when the hashed representation changes, so old chains stay verifiable
#: against the rules they were written under.
CHAIN_VERSION = "vigil-audit/1"

GENESIS = "0" * 64


class TamperError(AssertionError):
    """Raised when the chain does not verify."""


def _canonical(payload: Any) -> str:
    """Stable JSON: sorted keys, no incidental whitespace.

    The hash must not depend on dictionary ordering, or a re-serialisation with
    a different Python version would look like tampering.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class AuditEntry:
    """One immutable record."""

    index: int
    at: str
    actor: str                    # "vigil-triage/0.2.0" or a clinician reference
    action: str                   # assessment | override | acknowledge | observation | seen
    subject: str                  # pseudonymous patient reference
    payload: dict[str, Any] = field(default_factory=dict)
    prev_hash: str = GENESIS
    chain_version: str = CHAIN_VERSION
    entry_hash: str = ""

    def compute_hash(self) -> str:
        body = {
            "index": self.index, "at": self.at, "actor": self.actor,
            "action": self.action, "subject": self.subject,
            "payload": self.payload, "prev_hash": self.prev_hash,
            "chain_version": self.chain_version,
        }
        return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditLog:
    """Append-only, hash-chained record of everything the system did.

    Append-only is enforced by the interface: there is no update and no delete.
    A correction is a *new* entry that supersedes an old one, which is how
    clinical records work anyway - you do not erase a note, you write another.
    """

    entries: list[AuditEntry] = field(default_factory=list)

    @property
    def head(self) -> str:
        return self.entries[-1].entry_hash if self.entries else GENESIS

    def append(
        self, *, actor: str, action: str, subject: str,
        payload: dict[str, Any] | None = None, at: datetime | None = None,
    ) -> AuditEntry:
        entry = AuditEntry(
            index=len(self.entries),
            at=(at or datetime.now()).isoformat(),
            actor=actor, action=action, subject=subject,
            payload=payload or {}, prev_hash=self.head,
        )
        entry = AuditEntry(**{**entry.to_dict(), "entry_hash": entry.compute_hash()})
        self.entries.append(entry)
        return entry

    # ── verification ──────────────────────────────────────────────────────────
    def verify(self) -> tuple[bool, str]:
        """Re-derive every hash. Returns ``(ok, message)``.

        Names the first bad index rather than merely failing, because "the log
        was altered" is useless without "and here is where".
        """
        prev = GENESIS
        for e in self.entries:
            if e.prev_hash != prev:
                return False, f"broken link at index {e.index}: prev_hash does not match entry {e.index - 1}"
            if e.entry_hash != e.compute_hash():
                return False, f"content altered at index {e.index}: hash does not match its payload"
            prev = e.entry_hash
        return True, f"chain intact: {len(self.entries)} entries, head {self.head[:12]}"

    def assert_intact(self) -> None:
        ok, msg = self.verify()
        if not ok:
            raise TamperError(msg)

    # ── persistence ───────────────────────────────────────────────────────────
    def write(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as fh:
            for e in self.entries:
                fh.write(_canonical(e.to_dict()) + "\n")
        return p

    @classmethod
    def read(cls, path: str | Path) -> AuditLog:
        log = cls()
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                log.entries.append(AuditEntry(**json.loads(line)))
        return log

    # ── views ─────────────────────────────────────────────────────────────────
    def for_subject(self, subject: str) -> list[AuditEntry]:
        return [e for e in self.entries if e.subject == subject]

    def overrides(self) -> list[AuditEntry]:
        return [e for e in self.entries if e.action == "override"]

    def __iter__(self) -> Iterator[AuditEntry]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)


# ── the clinical record helpers ───────────────────────────────────────────────

def record_assessment(log: AuditLog, assessment, *, at: datetime | None = None) -> AuditEntry:
    """Log an automated assessment, with enough detail to reconstruct it.

    Stores the inputs that drove the decision and the decision itself, so a
    reviewer can re-run the engine at the recorded version and get the same
    answer. A recommendation nobody can reproduce is not reviewable, and an
    unreviewable automated decision is not defensible.
    """
    d = assessment.to_dict()
    return log.append(
        actor=assessment.engine_version, action="assessment",
        subject=assessment.patient_id,
        at=at or assessment.assessed_at,
        payload={
            "recommended_level": d["recommended_level"],
            "computed_level": d["computed_level"],
            "nurse_acuity": d["nurse_acuity"],
            "escalated": d["escalated"],
            "instrument": d["instrument"],
            "score_total": d["score_total"],
            "score_components": d["score_components"],
            "score_missing": d["score_missing"],
            "red_flags": [f["id"] for f in d["red_flags"]],
            "confidence_level": d["confidence_level"],
            "confidence_score": d["confidence_score"],
            "reassess_within_minutes": d["reassess_within_minutes"],
            "rationale": d["rationale"],
        },
    )


def record_override(
    log: AuditLog, *, patient_id: str, clinician: str, was: int, now_level: int,
    reason: str, recommendation_rationale: tuple[str, ...] = (), at: datetime | None = None,
) -> AuditEntry:
    """Log a clinician override.

    Records what the system said *and why* alongside what the clinician decided
    and why. Both halves are needed: reviewing an override without the
    reasoning it overruled tells you nothing about whether either was right.
    """
    return log.append(
        actor=clinician, action="override", subject=patient_id, at=at,
        payload={
            "from_level": was, "to_level": now_level, "reason": reason,
            "direction": "escalation" if now_level < was else "de-escalation" if now_level > was else "confirmation",
            "superseded_rationale": list(recommendation_rationale),
            "note": "clinician decision is authoritative and supersedes the recommendation",
        },
    )
