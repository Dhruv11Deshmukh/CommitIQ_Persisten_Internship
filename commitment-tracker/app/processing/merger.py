"""
Layer 2: Processing & Analytics - Commitment merger.

Scans existing OPEN/AT_RISK commitments for near-duplicates that slipped
past the creation-time duplicate check - most commonly the same task
described in two different emails where one has a due date and the other
doesn't (so the exact-match duplicate check didn't catch it). Merges them
into a single canonical row instead of leaving two.
"""
from difflib import SequenceMatcher
from sqlalchemy.orm import Session
from app.models import Commitment, AuditLog, CommitmentStatus

MERGE_SIMILARITY_THRESHOLD = 0.6


def _same_owner(a: str, b: str) -> bool:
    return a.strip().lower() == b.strip().lower()


def find_and_merge_duplicates(db: Session):
    """Returns a list of {"kept_id": ..., "removed_id": ...} for anything
    merged, so the caller can broadcast the right WebSocket events."""
    candidates = (
        db.query(Commitment)
        .filter(Commitment.status.in_([CommitmentStatus.open, CommitmentStatus.at_risk]))
        .order_by(Commitment.created_at.asc())
        .all()
    )

    merged_ids = set()
    results = []

    for i, a in enumerate(candidates):
        if a.id in merged_ids:
            continue

        for b in candidates[i + 1:]:
            if b.id in merged_ids:
                continue
            if not _same_owner(a.owner, b.owner):
                continue

            beneficiary_a = (a.beneficiary or "").strip().lower()
            beneficiary_b = (b.beneficiary or "").strip().lower()
            if beneficiary_a and beneficiary_b and beneficiary_a != beneficiary_b:
                continue

            similarity = SequenceMatcher(None, a.description.lower(), b.description.lower()).ratio()
            if similarity < MERGE_SIMILARITY_THRESHOLD:
                continue

            # a (created first) is canonical - fold in whatever b adds
            if a.due_date is None and b.due_date is not None:
                a.due_date = b.due_date
            if not a.beneficiary and b.beneficiary:
                a.beneficiary = b.beneficiary

            db.add(AuditLog(
                commitment_id=a.id,
                event="merged",
                detail=f"Merged commitment {b.id} ('{b.description}') into this one",
            ))

            db.query(AuditLog).filter(AuditLog.commitment_id == b.id).delete()
            db.delete(b)
            merged_ids.add(b.id)

            results.append({"kept_id": a.id, "removed_id": b.id})

    db.commit()
    return results