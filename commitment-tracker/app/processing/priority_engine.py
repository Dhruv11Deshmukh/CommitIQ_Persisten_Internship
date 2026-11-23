"""
Layer 2: Processing & Analytics - Priority & Ranking Engine.

Computes a transparent 0-100 urgency score for every commitment, plus a short
human-readable reason ("Overdue 2d · Important · Clash"). Purely rule-based -
no LLM calls - so it's instant, free, and always explainable.

Score = deadline urgency (the main driver) + boosts:

    overdue                              60  (+1 per 12h overdue, up to +5)
    due within 6h / 24h / 3d / 7d / 14d  55 / 45 / 32 / 20 / 10
    due later than that                  4
    no due date                          3
    marked Important                     +20
    part of a clash                      +10
    flagged at risk / marked overdue     +10
    owned by you                         +5   (what YOU must do outranks what
                                               others owe you, all else equal)
    unreliable track record              up to +10 (people who often miss
                                               deadlines get MORE visibility)

Capped at 100. Done/cancelled commitments score 0.

Date-only deadlines (no time of day) count as the END of that day, so a
deadline of "Sep 25" isn't overdue at 12:01 AM on the 25th.

Due dates are stored as naive local wall-clock times, so "now" is local time.
"""
import os
from datetime import datetime, timedelta

from dotenv import load_dotenv

from app.models import Commitment, CommitmentStatus, TrustScore

load_dotenv()

OWNER_NAME = os.environ.get("OWNER_NAME", "").strip().lower()

SCORED_STATUSES = [CommitmentStatus.open, CommitmentStatus.at_risk, CommitmentStatus.overdue]

# --- Tunable weights -------------------------------------------------------
OVERDUE_BASE = 60
OVERDUE_MAX_EXTRA = 5
HOURS_PER_EXTRA_POINT = 12

# (deadline within N hours, points, label) - first match wins
URGENCY_TIERS = [
    (6, 55, "Due within 6h"),
    (24, 45, "Due within 24h"),
    (72, 32, "Due in 3 days"),
    (168, 20, "Due this week"),
    (336, 10, "Due in 2 weeks"),
]
LATER_POINTS = 4
NO_DEADLINE_POINTS = 3

IMPORTANT_BOOST = 20
CLASH_BOOST = 10
FLAGGED_BOOST = 10
OWNED_BY_YOU_BOOST = 5

# Trust: below TRUST_THRESHOLD a person's commitments get a boost that grows
# as their score falls (0 at the threshold, TRUST_BOOST_MAX at a score of 0).
# Ignored until we've seen MIN_TRUST_SAMPLES outcomes, so one slip can't label anyone.
TRUST_THRESHOLD = 0.8
TRUST_BOOST_MAX = 10
MIN_TRUST_SAMPLES = 3

MAX_SCORE = 100
# ---------------------------------------------------------------------------


def effective_deadline(due_date: datetime) -> datetime:
    """Midnight means 'no time mentioned' -> treat as end of that day."""
    if due_date.hour == 0 and due_date.minute == 0:
        return due_date + timedelta(hours=23, minutes=59)
    return due_date


def _overdue_label(hours_over: float) -> str:
    if hours_over < 24:
        return f"Overdue {max(1, int(hours_over))}h"
    return f"Overdue {int(hours_over // 24)}d"


def score_commitment(c: Commitment, now: datetime = None, trust: dict = None):
    """Returns (score, reason) for one commitment. Pure function - no DB access.
    `trust` maps lowercase owner name -> (trust_score 0-1, number_of_outcomes)."""
    now = now or datetime.now()

    if c.status not in SCORED_STATUSES:
        return 0.0, None

    score = 0.0
    reasons = []

    if c.due_date is None:
        score += NO_DEADLINE_POINTS
    else:
        hours_left = (effective_deadline(c.due_date) - now).total_seconds() / 3600
        if hours_left < 0:
            hours_over = -hours_left
            score += OVERDUE_BASE + min(OVERDUE_MAX_EXTRA, int(hours_over // HOURS_PER_EXTRA_POINT))
            reasons.append(_overdue_label(hours_over))
        else:
            for limit_hours, points, label in URGENCY_TIERS:
                if hours_left <= limit_hours:
                    score += points
                    reasons.append(label)
                    break
            else:
                score += LATER_POINTS

    if c.is_important:
        score += IMPORTANT_BOOST
        reasons.append("Important")

    if c.clash_group_id:
        score += CLASH_BOOST
        reasons.append("Clash")

    if c.status == CommitmentStatus.at_risk:
        score += FLAGGED_BOOST
        reasons.append("Flagged at risk")
    elif c.status == CommitmentStatus.overdue:
        score += FLAGGED_BOOST
        reasons.append("Marked overdue")

    if trust:
        info = trust.get(c.owner.strip().lower())
        if info:
            trust_score, samples = info
            if samples >= MIN_TRUST_SAMPLES and trust_score < TRUST_THRESHOLD:
                boost = round(TRUST_BOOST_MAX * (TRUST_THRESHOLD - trust_score) / TRUST_THRESHOLD)
                if boost > 0:
                    score += boost
                    reasons.append(f"Unreliable history ({round(trust_score * 100)}%)")

    if OWNER_NAME and c.owner.strip().lower() == OWNER_NAME:
        score += OWNED_BY_YOU_BOOST
        reasons.append("You owe this")

    return round(min(score, MAX_SCORE), 1), (" · ".join(reasons) or None)


def update_priorities(db, now: datetime = None):
    """Recomputes score + reason for every commitment and saves only the ones
    that actually changed. Returns the list of changed commitment ids."""
    now = now or datetime.now()
    changed = []

    trust = {
        t.person.strip().lower(): (
            t.score,
            (t.completed_count or 0) + (t.late_count or 0) + (t.missed_count or 0),
        )
        for t in db.query(TrustScore).all()
        if t.score is not None
    }

    for c in db.query(Commitment).all():
        score, reason = score_commitment(c, now, trust)
        if (c.priority_score or 0.0) != score or c.priority_reason != reason:
            c.priority_score = score
            c.priority_reason = reason
            changed.append(c.id)

    db.commit()
    return changed