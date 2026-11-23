"""
Layer 2: Processing & Analytics - Clash Detection Engine (time-aware).

Clash rules, applied per owner:

1. TIMED commitments (due date has a real time of day, e.g. 6:00 PM) clash
   when their times fall within CLASH_WINDOW_MINUTES of each other. Chained:
   if A~B and B~C, all three form one clash group.
2. DATE-ONLY commitments (no time mentioned, stored as midnight) clash when
   two or more fall on the same calendar day - a "busy day" signal.

Timed and date-only commitments never clash with each other, since there's
no way to tell whether a date-only deadline overlaps a specific time.

Recomputed from scratch on every run, so stale flags clear themselves when
a due date changes or a commitment is completed/deleted.
"""
import os
from collections import defaultdict
from datetime import timedelta

from dotenv import load_dotenv
from sqlalchemy import or_

from app.models import Commitment, CommitmentStatus

load_dotenv()

CLASH_WINDOW_MINUTES = int(os.environ.get("CLASH_WINDOW_MINUTES", "90"))

OPEN_STATUSES = [CommitmentStatus.open, CommitmentStatus.at_risk]


def _has_time(dt) -> bool:
    """Midnight means 'no time mentioned' (date-only deadline)."""
    return dt.hour != 0 or dt.minute != 0


def _cluster_timed(timed_sorted, window):
    """Groups time-sorted commitments into chains where each consecutive
    gap is smaller than `window`. Returns only clusters of 2 or more."""
    clusters = []
    current = []
    for c in timed_sorted:
        if current and (c.due_date - current[-1].due_date) < window:
            current.append(c)
        else:
            if len(current) > 1:
                clusters.append(current)
            current = [c]
    if len(current) > 1:
        clusters.append(current)
    return clusters


def detect_clashes(db):
    """Returns a list of {"commitment_id": ..., "clash_group_id": ...} for
    every commitment whose clash status changed this run (clash_group_id is
    None when a clash was resolved)."""
    open_commitments = (
        db.query(Commitment)
        .filter(Commitment.status.in_(OPEN_STATUSES))
        .filter(Commitment.due_date.isnot(None))
        .all()
    )

    window = timedelta(minutes=CLASH_WINDOW_MINUTES)
    desired = {}  # commitment id -> clash_group_id (absent = no clash)

    by_owner = defaultdict(list)
    for c in open_commitments:
        by_owner[c.owner.strip().lower()].append(c)

    for owner_key, items in by_owner.items():
        timed = sorted((c for c in items if _has_time(c.due_date)), key=lambda c: c.due_date)
        untimed = [c for c in items if not _has_time(c.due_date)]

        for cluster in _cluster_timed(timed, window):
            group_id = f"{owner_key}-{cluster[0].due_date.strftime('%Y-%m-%dT%H:%M')}"
            for c in cluster:
                desired[c.id] = group_id

        by_day = defaultdict(list)
        for c in untimed:
            by_day[c.due_date.date()].append(c)
        for day, group in by_day.items():
            if len(group) > 1:
                group_id = f"{owner_key}-{day.isoformat()}"
                for c in group:
                    desired[c.id] = group_id

    changed = []

    for c in open_commitments:
        new_group = desired.get(c.id)
        if c.clash_group_id != new_group:
            c.clash_group_id = new_group
            changed.append({"commitment_id": c.id, "clash_group_id": new_group})

    # Clear stale flags on anything no longer a live conflict: completed or
    # cancelled commitments, or ones whose due date was removed.
    stale = (
        db.query(Commitment)
        .filter(Commitment.clash_group_id.isnot(None))
        .filter(or_(~Commitment.status.in_(OPEN_STATUSES), Commitment.due_date.is_(None)))
        .all()
    )
    for c in stale:
        c.clash_group_id = None
        changed.append({"commitment_id": c.id, "clash_group_id": None})

    db.commit()
    return changed