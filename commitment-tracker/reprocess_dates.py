"""
One-time backfill: corrects due dates on commitments that were extracted
BEFORE the reference-date fix was added to extractor.py. Re-fetches each
Gmail message's real send date, then re-runs extraction anchored to that
date, and updates any commitment whose due_date changes as a result.

Run once:
    python reprocess_dates.py

Safe to run multiple times - it fetches sent_at once per source (cached
after the first run) and re-checks due dates each time it's run.
"""
import time
from email.utils import parsedate_to_datetime
from sqlalchemy import text
from dateutil import parser as dateparser

from app.database import SessionLocal, engine
from app.models import Source, Commitment, AuditLog
from app.ingestion.gmail_connector import GmailConnector
from app.processing.extractor import extract_commitments


def ensure_sent_at_column():
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE sources ADD COLUMN IF NOT EXISTS sent_at TIMESTAMP"))
        conn.commit()


def run():
    ensure_sent_at_column()
    db = SessionLocal()
    connector = GmailConnector(max_results=1)

    sources = db.query(Source).filter(Source.channel == "gmail").all()
    print(f"Found {len(sources)} Gmail sources to check.")

    updated_commitments = 0
    checked = 0

    for source in sources:
        if not source.sent_at:
            try:
                msg = connector.service.users().messages().get(
                    userId="me", id=source.external_id, format="metadata",
                    metadataHeaders=["Date"],
                ).execute()
                headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
                date_header = headers.get("Date")
                if date_header:
                    source.sent_at = parsedate_to_datetime(date_header)
                    db.commit()
            except Exception as e:
                print(f"  Could not fetch send date for message {source.external_id}: {e}")
                continue

        if not source.sent_at:
            continue

        ref_date = source.sent_at.date().isoformat()
        checked += 1

        try:
            extracted = extract_commitments(source.raw_text, reference_date=ref_date)
        except Exception as e:
            print(f"  Extraction failed for source {source.id}: {e}")
            continue

        # Stay comfortably under the free tier's 15-requests-per-minute cap
        time.sleep(4.5)

        existing = db.query(Commitment).filter(Commitment.source_id == source.id).all()

        for i, item in enumerate(extracted):
            if i >= len(existing):
                break
            commitment = existing[i]

            new_due = None
            if item.due_date:
                try:
                    new_due = dateparser.parse(item.due_date)
                except (ValueError, TypeError):
                    new_due = None

            if new_due and new_due.date() != (commitment.due_date.date() if commitment.due_date else None):
                old_due = commitment.due_date
                commitment.due_date = new_due
                db.add(AuditLog(
                    commitment_id=commitment.id,
                    event="edited",
                    detail=f"due_date corrected from {old_due} to {new_due} (backfill, reference_date={ref_date})",
                ))
                updated_commitments += 1
                print(f"  Fixed commitment {commitment.id}: {old_due} -> {new_due}")

        db.commit()

    print(f"\nDone. Checked {checked} sources, corrected {updated_commitments} due dates.")
    db.close()


if __name__ == "__main__":
    run()