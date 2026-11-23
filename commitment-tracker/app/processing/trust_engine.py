"""
Layer 2: Processing & Analytics - Trust Scorer.

Tracks how reliably each person follows through, as a score from 0 to 1.

Three kinds of evidence, per person:
    on time   finished on or before the deadline          (full credit)
    late      finished, but after the deadline            (half credit)
    missed    still open and already past the deadline    (no credit)

Only commitments WITH a deadline count - there's no way to be late without one.

Why finished work is stored but misses are counted live
-------------------------------------------------------
Done commitments are auto-deleted after 24h, so their outcome is tallied once
into the TrustScore table (guarded by a per-commitment `trust_outcome` marker
so it can never be counted twice) and survives deletion. Misses, on the other
hand, are recounted from currently-overdue commitments on every run, so
deleting a bogus item or merging a duplicate never leaves a phantom miss.

The score is smoothed toward a neutral prior (0.8 = "reliable until proven
otherwise") so one early slip doesn't brand anyone, and newcomers aren't
penalised. Scores only influence priority after MIN_TRUST_SAMPLES outcomes.

Due dates are naive local times; completed_at is naive UTC - converted here.
"""
from collections import defaultdict
from datetime import datetime, timezone

from app.models import Commitment, CommitmentStatus, TrustScore
from app.processing.priority_engine import effective_deadline, MIN_TRUST_SAMPLES

PRIOR_SCORE = 0.8
PRIOR_WEIGHT = 2.0   # the prior counts as this many imaginary on-the-fence outcomes
LATE_CREDIT = 0.5

LIVE_STATUSES = [CommitmentStatus.open, CommitmentStatus.at_risk, CommitmentStatus.overdue]


def compute_trust(on_time: int, late: int, missed: int) -> float:
    total = on_time + late + missed
    return round((on_time + LATE_CREDIT * late + PRIOR_SCORE * PRIOR_WEIGHT) / (total + PRIOR_WEIGHT), 3)


def trust_label(score: float, samples: int) -> str:
    if samples < MIN_TRUST_SAMPLES:
        return "New"
    if score >= 0.8:
        return "Reliable"
    if score >= 0.6:
        return "Fair"
    return "Unreliable"


def _key(name: str) -> str:
    return name.strip().lower()


def _utc_naive_to_local(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


def finished_outcome(c: Commitment):
    """'on_time' | 'late' for a finished commitment, else None. Items marked
    done before completion times were recorded (no completed_at) are skipped
    rather than guessed at."""
    if c.status != CommitmentStatus.done or c.due_date is None or c.completed_at is None:
        return None
    finished = _utc_naive_to_local(c.completed_at)
    return "on_time" if finished <= effective_deadline(c.due_date) else "late"


def is_missed(c: Commitment, now: datetime) -> bool:
    return (
        c.status in LIVE_STATUSES
        and c.due_date is not None
        and effective_deadline(c.due_date) < now
    )


def _bump(t: TrustScore, outcome: str, delta: int):
    if outcome == "on_time":
        t.completed_count = max(0, (t.completed_count or 0) + delta)
    elif outcome == "late":
        t.late_count = max(0, (t.late_count or 0) + delta)


def update_trust(db, now: datetime = None):
    """Brings every person's trust record up to date. Idempotent: running it
    twice with no changes in between writes nothing. Returns the names of
    people whose record changed."""
    now = now or datetime.now()

    rows = {_key(t.person): t for t in db.query(TrustScore).all()}
    created = set()

    def get(owner: str) -> TrustScore:
        k = _key(owner)
        if k not in rows:
            t = TrustScore(
                person=owner.strip(), completed_count=0, late_count=0,
                missed_count=0, score=PRIOR_SCORE,
            )
            db.add(t)
            rows[k] = t
            created.add(k)
        return rows[k]

    live_missed = defaultdict(int)

    for c in db.query(Commitment).all():
        # 1. Finished work: tally once, keep forever (idempotent via the marker)
        new_outcome = finished_outcome(c)
        if new_outcome != c.trust_outcome:
            t = get(c.owner)
            if c.trust_outcome:
                _bump(t, c.trust_outcome, -1)
            if new_outcome:
                _bump(t, new_outcome, +1)
            c.trust_outcome = new_outcome

        # 2. Misses: counted live from what is overdue right now
        if is_missed(c, now):
            get(c.owner)
            live_missed[_key(c.owner)] += 1

    changed = []
    for k, t in rows.items():
        missed = live_missed.get(k, 0)
        score = compute_trust(t.completed_count or 0, t.late_count or 0, missed)
        if k in created or (t.missed_count or 0) != missed or t.score != score:
            t.missed_count = missed
            t.score = score
            changed.append(t.person)

    db.commit()
    return changed


def release_finished_outcome(db, c: Commitment):
    """Takes back a commitment's tallied outcome (used before its owner is
    changed, so the credit moves with it instead of staying with the old
    owner). Not committed - the caller commits."""
    if not c.trust_outcome:
        return
    t = db.query(TrustScore).all()
    for row in t:
        if _key(row.person) == _key(c.owner):
            _bump(row, c.trust_outcome, -1)
            break
    c.trust_outcome = None


def people_summary(db):
    """One row per person for the Overview 'People' card. Established people
    come first, least reliable at the top; people with too little history last."""
    open_counts = defaultdict(int)
    for c in db.query(Commitment).filter(Commitment.status.in_(LIVE_STATUSES)).all():
        open_counts[_key(c.owner)] += 1

    people = []
    for t in db.query(TrustScore).all():
        on_time, late, missed = t.completed_count or 0, t.late_count or 0, t.missed_count or 0
        samples = on_time + late + missed
        people.append({
            "person": t.person,
            "score": round((t.score if t.score is not None else PRIOR_SCORE) * 100),
            "label": trust_label(t.score if t.score is not None else PRIOR_SCORE, samples),
            "on_time": on_time,
            "late": late,
            "missed": missed,
            "open": open_counts.get(_key(t.person), 0),
            "samples": samples,
        })

    people.sort(key=lambda p: (p["samples"] < MIN_TRUST_SAMPLES, p["score"], p["person"]))
    return people