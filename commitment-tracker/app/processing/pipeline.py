"""
Shared ingestion pipeline: ANY source -> extraction -> Postgres.

Sources (Gmail, dropped transcript files, pasted text, later Teams) all hand
over the same RawMessage objects, so everything here is source-agnostic:
duplicate prevention, self-verification, audit logging.

Guarantees
----------
* One source failing never blocks another (each is fetched in its own try).
* A message is only marked as ingested once it is safely stored. If the AI
  service is unavailable the message is left untouched and retried on the
  next run - it is never silently recorded as "processed, nothing found".
* One source can't be ingested by two threads at once (per-source lock), so
  the manual "Sync now" and the background loop can't double-process.
"""
import threading
from datetime import datetime
from difflib import SequenceMatcher
from typing import Dict, List, Optional

from dateutil import parser as dateparser

from app.database import SessionLocal
from app.ingestion.base import BaseConnector
from app.ingestion.registry import build_connectors
from app.models import AuditLog, Commitment, CommitmentStatus, Source
from app.processing.extractor import ExtractionUnavailable, extract_commitments
from app.processing.verifier import check_for_completion

DUPLICATE_SIMILARITY_THRESHOLD = 0.7

_locks_guard = threading.Lock()
_channel_locks: Dict[str, threading.Lock] = {}


def _lock_for(channel: str) -> threading.Lock:
    with _locks_guard:
        return _channel_locks.setdefault(channel, threading.Lock())


def _is_duplicate(db, owner: str, description: str, due) -> bool:
    candidates = (
        db.query(Commitment)
        .filter(
            Commitment.owner == owner,
            Commitment.status.in_([CommitmentStatus.open, CommitmentStatus.at_risk]),
        )
        .all()
    )

    for c in candidates:
        similarity = SequenceMatcher(None, c.description.lower(), description.lower()).ratio()

        same_due_date = (
            (c.due_date is None and due is None)
            or (c.due_date is not None and due is not None and c.due_date.date() == due.date())
        )

        if similarity >= DUPLICATE_SIMILARITY_THRESHOLD and same_due_date:
            return True

    return False


def _check_self_verification(db, owner: str, text: str, source_external_id: str, exclude_ids=()):
    """Checks whether this message reports completion of one of owner's
    EARLIER open commitments. If so, marks it done and returns a dict
    describing the update (for WebSocket broadcasting), else None.

    exclude_ids: commitments created from this very message - a message that
    makes a promise can't also be the proof it was kept."""
    open_commitments = [
        c for c in (
            db.query(Commitment)
            .filter(
                Commitment.owner == owner,
                Commitment.status.in_([CommitmentStatus.open, CommitmentStatus.at_risk]),
            )
            .all()
        )
        if c.id not in set(exclude_ids)
    ]

    if not open_commitments:
        return None

    candidates = [{"id": c.id, "description": c.description} for c in open_commitments]

    try:
        completed_id = check_for_completion(owner, candidates, text)
    except Exception as e:
        print(f"Self-verification check failed: {e}")
        return None

    if not completed_id:
        return None

    commitment = next((c for c in open_commitments if c.id == completed_id), None)
    if not commitment:
        return None

    commitment.status = CommitmentStatus.done
    commitment.completed_at = datetime.utcnow()
    db.add(AuditLog(
        commitment_id=commitment.id,
        event="auto_verified",
        detail=f"Auto-marked done based on message {source_external_id}",
    ))
    db.commit()

    return {"id": commitment.id, "owner": commitment.owner, "description": commitment.description}


def _ingest_from(connector: BaseConnector) -> dict:
    """Runs one source to completion. Never raises for a source-level problem."""
    result = {"created": [], "auto_verified": [], "skipped": 0, "deferred": 0}
    channel = connector.channel_name

    try:
        messages = connector.fetch_new_messages()
    except Exception as e:
        print(f"[{channel}] fetch failed: {e}")
        return result

    db = SessionLocal()
    try:
        for index, msg in enumerate(messages):
            already = db.query(Source).filter(
                Source.channel == msg.channel,
                Source.external_id == msg.external_id,
            ).first()
            if already:
                result["skipped"] += 1
                connector.acknowledge(msg)
                continue

            # Extract BEFORE writing anything, so an outage leaves no trace.
            try:
                extracted = extract_commitments(
                    msg.text, author=msg.author, reference_date=msg.sent_at, kind=msg.kind,
                )
            except ExtractionUnavailable as e:
                remaining = len(messages) - index
                print(f"[{channel}] AI service unavailable - {remaining} message(s) left for the next run: {e}")
                result["deferred"] = remaining
                break  # no point hammering a service that's down for the rest of the queue
            except Exception as e:
                # A message the model can't handle at all must not block the queue forever.
                print(f"[{channel}] extraction failed for {msg.external_id}: {e}")
                extracted = []

            source = Source(
                channel=msg.channel,
                external_id=msg.external_id,
                raw_text=msg.text,
                sent_at=dateparser.parse(msg.sent_at) if msg.sent_at else None,
            )
            db.add(source)
            db.flush()

            new_ids = []
            for item in extracted:
                due = None
                if item.due_date:
                    try:
                        due = dateparser.parse(item.due_date)
                    except (ValueError, TypeError, OverflowError):
                        due = None

                if _is_duplicate(db, item.owner, item.description, due):
                    db.add(AuditLog(
                        commitment_id=None,
                        event="duplicate_skipped",
                        detail=f"Skipped likely-duplicate commitment from {msg.channel} message "
                               f"{msg.external_id}: {item.owner} -> {item.description}",
                    ))
                    continue

                commitment = Commitment(
                    source_id=source.id,
                    owner=item.owner,
                    owner_email=msg.author_email,
                    beneficiary=item.beneficiary,
                    beneficiary_email=msg.recipient_email,
                    description=item.description,
                    due_date=due,
                    is_meeting=bool(item.is_meeting),
                )
                db.add(commitment)
                db.flush()
                db.add(AuditLog(
                    commitment_id=commitment.id,
                    event="created",
                    detail=f"extracted from {msg.channel} message {msg.external_id}",
                ))
                new_ids.append(commitment.id)
                result["created"].append({
                    "id": commitment.id,
                    "owner": commitment.owner,
                    "description": commitment.description,
                    "source": msg.channel,
                })

            db.commit()
            connector.acknowledge(msg)  # only now is it safe to clean up the original

            # Did this message report finishing one of the author's EXISTING commitments?
            if msg.author:
                done = _check_self_verification(db, msg.author, msg.text, msg.external_id, exclude_ids=new_ids)
                if done:
                    result["auto_verified"].append(done)
    finally:
        db.close()

    return result


def run_ingestion(
    max_emails: int = 10,
    connectors: Optional[List[BaseConnector]] = None,
    sources: Optional[List[str]] = None,
):
    """Ingests from every enabled source (or the given connectors / source names).

    Returns {"created", "auto_verified", "skipped", "deferred"}:
      skipped  = messages already ingested earlier
      deferred = messages left for the next run because the AI service was down
    """
    if connectors is None:
        connectors = build_connectors(sources, max_emails)

    total = {"created": [], "auto_verified": [], "skipped": 0, "deferred": 0}
    for connector in connectors:
        with _lock_for(connector.channel_name):
            one = _ingest_from(connector)
        total["created"] += one["created"]
        total["auto_verified"] += one["auto_verified"]
        total["skipped"] += one["skipped"]
        total["deferred"] += one["deferred"]
    return total


if __name__ == "__main__":
    out = run_ingestion()
    print(f"Created {len(out['created'])} commitments "
          f"({out['skipped']} already seen, {out['deferred']} deferred).")
    for r in out["created"]:
        print(f"  [{r['source']}] {r['owner']} -> {r['description']}")
    print(f"Auto-verified {len(out['auto_verified'])} commitments as done.")