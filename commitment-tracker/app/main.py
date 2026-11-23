"""
Entry point for the API. Run with:
    uvicorn app.main:app --reload

Background loops started on boot (sources come from ENABLED_SOURCES in .env):
1. Gmail polling   - new emails -> extraction, self-verification, clash
                     detection, conflict-notice drafts, priority scoring
2. Auto-cleanup    - deletes commitments "done" for 24+ hours
3. Merge check     - merges near-duplicate commitments
4. Nudge check     - drafts follow-ups for overdue / due-soon commitments
5. Priority check  - re-scores urgency + trust as time passes
6. File drop       - picks up dropped transcript / chat files within seconds
7. Meeting check   - checks your calendar for meetings and drafts the availability email
                     (only when MEETINGS_ENABLED=true)
"""
import os
import asyncio
import hashlib
import time
from datetime import date, datetime, timedelta
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import Base, engine, get_db
from app.ingestion.base import InMemoryConnector, RawMessage
from app.ingestion.registry import enabled_source_names
from app.integrations.google_auth import meetings_enabled
from app.integrations.google_calendar import CalendarUnavailable, GoogleCalendar
from app.models import Commitment, AuditLog, CommitmentStatus
from app.schemas import CommitmentOut, CommitmentUpdate
from app.presentation.connection_manager import manager
from app.processing.pipeline import run_ingestion
from app.processing.merger import find_and_merge_duplicates
from app.processing.clash_detector import detect_clashes
from app.processing.nudger import find_and_draft_nudges
from app.processing import meeting_agent
from app.processing.priority_engine import update_priorities
from app.processing.trust_engine import update_trust, release_finished_outcome, people_summary
from app.processing.reschedule_notifier import generate_apology_body, generate_conflict_notice_body
from app.notifications import models as _notif_models
from app.notifications.router import router as notifications_router
from app.notifications.generator import notification_loop
from app.dependencies import models as _dep_models
from app.dependencies.router import router as dependencies_router, cascade_loop
from app.briefs import models as _brief_models
from app.briefs.router import router as briefs_router
from app.briefs.service import brief_loop
from app.notifications.chat import router as assistant_router
from app.notifications.chat import router as assistant_router
from app.notifications.popup import popup_loop
from app.auth import models as _auth_models
from app.auth.router import router as auth_router
from app.dependencies import models_smart as _dep_smart_models
from app.dependencies.smart import router as smart_dependencies_router
from app.dependencies.auto import auto_loop as dependency_auto_loop
from app.dependencies import models_smart as _dep_smart_models
from app.dependencies.smart import router as smart_dependencies_router
from app.dependencies.auto import auto_loop as dependency_auto_loop
from app.dependencies.reminders import reminder_loop as dependency_reminder_loop

Base.metadata.create_all(bind=engine)

POLL_INTERVAL = int(os.environ.get("GMAIL_POLL_INTERVAL_SECONDS", "60"))
AUTO_DELETE_CHECK_INTERVAL = int(os.environ.get("AUTO_DELETE_CHECK_INTERVAL_SECONDS", "300"))
AUTO_DELETE_AFTER_HOURS = int(os.environ.get("AUTO_DELETE_AFTER_HOURS", "24"))
MERGE_CHECK_INTERVAL = int(os.environ.get("MERGE_CHECK_INTERVAL_SECONDS", "600"))
NUDGE_CHECK_INTERVAL = int(os.environ.get("NUDGE_CHECK_INTERVAL_SECONDS", "1800"))
PRIORITY_CHECK_INTERVAL = int(os.environ.get("PRIORITY_CHECK_INTERVAL_SECONDS", "300"))
FILE_DROP_POLL_SECONDS = float(os.environ.get("FILE_DROP_POLL_SECONDS", "5"))
MEETING_CHECK_INTERVAL = int(os.environ.get("MEETING_CHECK_INTERVAL_SECONDS", "60"))

# Sources polled on a timer (Gmail, later Teams). The file-drop source has its own fast loop.
POLLED_SOURCES = [name for name in enabled_source_names() if name != "file"]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _gmail():
    from app.ingestion.gmail_connector import GmailConnector
    return GmailConnector(max_results=1)


def _commitment_or_404(db: Session, commitment_id: int) -> Commitment:
    commitment = db.query(Commitment).filter(Commitment.id == commitment_id).first()
    if not commitment:
        raise HTTPException(status_code=404, detail="Commitment not found")
    return commitment


def _delete_commitment_sync(db: Session, commitment: Commitment):
    """Deletes a commitment, its audit log rows, and any pending Gmail draft
    associated with it. Used by manual delete, auto-delete, and merge."""
    description = commitment.description
    owner = commitment.owner
    commitment_id = commitment.id
    draft_ids = [d for d in (commitment.gmail_draft_id, commitment.meeting_draft_id) if d]

    if draft_ids:
        try:
            connector = _gmail()
            for draft_id in draft_ids:
                connector.delete_draft(draft_id)
        except Exception as e:
            print(f"Failed to delete draft(s) {draft_ids} for commitment {commitment_id}: {e}")

    db.query(AuditLog).filter(AuditLog.commitment_id == commitment_id).delete()
    db.delete(commitment)
    db.add(AuditLog(commitment_id=None, event="deleted", detail=f"Deleted: {owner} -> {description}"))
    db.commit()
    return commitment_id


async def _handle_new_clashes(db: Session, clash_changes):
    """For each commitment that just GAINED a clash, drafts a heads-up
    conflict notice immediately - only for the non-important side of the
    conflict, and only once (skips if a draft already exists)."""
    for change in clash_changes:
        if not change["clash_group_id"]:
            continue  # this entry lost its clash, nothing to draft

        commitment = db.query(Commitment).filter(Commitment.id == change["commitment_id"]).first()
        if not commitment:
            continue
        if commitment.is_important:
            continue
        if commitment.gmail_draft_id:
            continue
        if not commitment.beneficiary_email:
            continue

        def _draft():
            from app.ingestion.gmail_connector import GmailConnector
            body = generate_conflict_notice_body(commitment.description, commitment.due_date)
            if not body:
                return None
            connector = GmailConnector(max_results=1)
            return connector.create_draft(
                commitment.beneficiary_email,
                f"Possible delay: {commitment.description}",
                body,
            )

        try:
            draft_id = await asyncio.to_thread(_draft)
            if draft_id:
                commitment.gmail_draft_id = draft_id
                commitment.last_nudged_at = datetime.utcnow()
                db.add(AuditLog(
                    commitment_id=commitment.id,
                    event="clash_conflict_drafted",
                    detail=f"Drafted conflict heads-up - clashes with another commitment on {commitment.due_date}",
                ))
                db.commit()
                await manager.broadcast({"type": "nudge_drafted", "commitment_id": commitment.id})
        except Exception as e:
            print(f"Failed to draft clash conflict notice for commitment {commitment.id}: {e}")


async def _handle_resolved_clashes(db: Session, clash_changes):
    """When a commitment's clash resolves (clash_group_id -> None), deletes
    the pending draft attached to it - it's stale once the conflict is gone."""
    for change in clash_changes:
        if change["clash_group_id"] is not None:
            continue  # this entry GAINED a clash, handled elsewhere

        commitment = db.query(Commitment).filter(Commitment.id == change["commitment_id"]).first()
        if not commitment or not commitment.gmail_draft_id:
            continue

        draft_id = commitment.gmail_draft_id

        def _delete():
            from app.ingestion.gmail_connector import GmailConnector
            connector = GmailConnector(max_results=1)
            connector.delete_draft(draft_id)

        try:
            await asyncio.to_thread(_delete)
            commitment.gmail_draft_id = None
            db.add(AuditLog(
                commitment_id=commitment.id,
                event="draft_auto_deleted",
                detail="Clash resolved - removed the associated draft",
            ))
            db.commit()
            await manager.broadcast({"type": "commitment_updated", "commitment_id": commitment.id})
        except Exception as e:
            print(f"Failed to auto-delete draft after clash resolved for commitment {commitment.id}: {e}")


async def _refresh_priorities(db: Session):
    """Re-scores everything and sends ONE broadcast if anything changed
    (the frontend refetches the whole list on any message)."""
    trust_changed = update_trust(db)   # trust first: priority scoring reads it
    changed = update_priorities(db)
    if changed or trust_changed:
        await manager.broadcast({"type": "priorities_updated", "count": len(changed)})
    return changed


# ---------------------------------------------------------------------------
# Background loops
# ---------------------------------------------------------------------------

async def _after_ingestion(result):
    """Everything that follows new data, whatever source it came from: live
    updates to the dashboard, clash detection, conflict drafts, re-scoring."""
    for c in result["created"]:
        await manager.broadcast({"type": "commitment_created", **c})
    for c in result["auto_verified"]:
        await manager.broadcast({"type": "commitment_updated", "commitment_id": c["id"]})

    from app.database import SessionLocal
    db = SessionLocal()
    try:
        clash_changes = detect_clashes(db)
        for c in clash_changes:
            await manager.broadcast({"type": "commitment_updated", "commitment_id": c["commitment_id"]})
        await _handle_new_clashes(db, clash_changes)
        await _handle_resolved_clashes(db, clash_changes)
        await _refresh_priorities(db)
    finally:
        db.close()


async def poll_sources_loop():
    """Polls the timer-based sources (Gmail, later Teams) every POLL_INTERVAL seconds."""
    while True:
        try:
            result = await asyncio.to_thread(run_ingestion, 10, None, POLLED_SOURCES)
            await _after_ingestion(result)
        except Exception as e:
            print(f"Source polling error: {e}")
        await asyncio.sleep(POLL_INTERVAL)


async def file_drop_loop():
    """Watches the drop folder. The check is a cheap directory listing every
    few seconds; real work only happens when a file is waiting."""
    try:
        from app.ingestion.file_connector import FileDropConnector
        connector = FileDropConnector()
    except Exception as e:
        print(f"[file] could not start the drop-folder watcher: {e}")
        return
    print(f"[file] watching {connector.drop_dir} for transcripts and chat exports")

    while True:
        delay = FILE_DROP_POLL_SECONDS
        try:
            if await asyncio.to_thread(connector.has_pending):
                result = await asyncio.to_thread(run_ingestion, 10, [connector])
                if result["created"] or result["auto_verified"]:
                    await _after_ingestion(result)
                if result["deferred"]:
                    delay = max(delay, 60)  # AI service is down: back off instead of retrying every few seconds
        except Exception as e:
            print(f"File drop error: {e}")
        await asyncio.sleep(delay)


_meeting_problem = {"message": None, "at": 0.0}


def _log_meeting_problem(message: str):
    """A calendar problem repeats every cycle; report each distinct one once,
    then at most every 10 minutes, instead of flooding the terminal."""
    now = time.monotonic()
    if message != _meeting_problem["message"] or now - _meeting_problem["at"] > 600:
        print(f"[meetings] {message}")
        _meeting_problem.update(message=message, at=now)


def _sync_meeting_time(db: Session, commitment: Commitment):
    calendar = GoogleCalendar() if commitment.calendar_status == "scheduled" else None
    gmail = _gmail() if commitment.meeting_draft_id else None
    return meeting_agent.handle_time_change(db, calendar, gmail, commitment)


def _run_meeting_checks(limit: int = 5):
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        if not meeting_agent.pending_checks(db, limit=limit):
            return []  # nothing to do, so don't even touch Google
        return meeting_agent.find_and_check_meetings(db, GoogleCalendar(), _gmail(), limit=limit)
    finally:
        db.close()


async def _announce_meeting_checks(results):
    for r in results:
        await manager.broadcast({
            "type": "nudge_drafted" if r["drafted"] else "commitment_updated",
            "commitment_id": r["id"],
        })


async def meeting_loop():
    """Looks at newly detected meetings: checks your calendar, drafts the email."""
    while True:
        try:
            await _announce_meeting_checks(await asyncio.to_thread(_run_meeting_checks))
        except CalendarUnavailable as e:
            _log_meeting_problem(str(e))
        except Exception as e:
            _log_meeting_problem(f"{type(e).__name__}: {e}")
        await asyncio.sleep(MEETING_CHECK_INTERVAL)


async def auto_delete_loop():
    while True:
        try:
            from app.database import SessionLocal
            db = SessionLocal()
            cutoff = datetime.utcnow() - timedelta(hours=AUTO_DELETE_AFTER_HOURS)

            expired = (
                db.query(Commitment)
                .filter(Commitment.status == CommitmentStatus.done)
                .filter(Commitment.completed_at.isnot(None))
                .filter(Commitment.completed_at <= cutoff)
                .all()
            )

            for commitment in expired:
                commitment_id = _delete_commitment_sync(db, commitment)
                await manager.broadcast({"type": "commitment_deleted", "commitment_id": commitment_id})

            db.close()
        except Exception as e:
            print(f"Auto-delete error: {e}")

        await asyncio.sleep(AUTO_DELETE_CHECK_INTERVAL)


async def merge_loop():
    while True:
        try:
            from app.database import SessionLocal
            db = SessionLocal()
            merges = find_and_merge_duplicates(db)
            for m in merges:
                await manager.broadcast({"type": "commitment_deleted", "commitment_id": m["removed_id"]})
                await manager.broadcast({"type": "commitment_updated", "commitment_id": m["kept_id"]})
            db.close()
        except Exception as e:
            print(f"Merge check error: {e}")
        await asyncio.sleep(MERGE_CHECK_INTERVAL)


async def nudge_loop():
    while True:
        try:
            from app.database import SessionLocal
            from app.ingestion.gmail_connector import GmailConnector
            db = SessionLocal()
            connector = GmailConnector(max_results=1)
            drafted = find_and_draft_nudges(db, connector)
            for commitment_id in drafted:
                await manager.broadcast({"type": "nudge_drafted", "commitment_id": commitment_id})
            db.close()
        except Exception as e:
            print(f"Nudge check error: {e}")
        await asyncio.sleep(NUDGE_CHECK_INTERVAL)


async def priority_loop():
    """Urgency changes just because time passes (a task due in 25h becomes
    due in 23h), so scores are refreshed on a timer, not only on edits."""
    while True:
        try:
            from app.database import SessionLocal
            db = SessionLocal()
            await _refresh_priorities(db)
            db.close()
        except Exception as e:
            print(f"Priority check error: {e}")
        await asyncio.sleep(PRIORITY_CHECK_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [
        asyncio.create_task(auto_delete_loop()),
        asyncio.create_task(merge_loop()),
        asyncio.create_task(nudge_loop()),
        asyncio.create_task(priority_loop()),
    ]
    tasks.append(asyncio.create_task(notification_loop()))
    tasks.append(asyncio.create_task(cascade_loop()))
    tasks.append(asyncio.create_task(brief_loop()))
    tasks.append(asyncio.create_task(popup_loop()))
    tasks.append(asyncio.create_task(dependency_auto_loop()))
    tasks.append(asyncio.create_task(dependency_reminder_loop()))
    if POLLED_SOURCES:
        tasks.append(asyncio.create_task(poll_sources_loop()))
    if "file" in enabled_source_names():
        tasks.append(asyncio.create_task(file_drop_loop()))
    if meetings_enabled():
        tasks.append(asyncio.create_task(meeting_loop()))
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Commitment Tracking Platform", lifespan=lifespan)
app.include_router(notifications_router)
app.include_router(dependencies_router)
app.include_router(smart_dependencies_router)
app.include_router(briefs_router)
app.include_router(assistant_router)
app.include_router(assistant_router)
app.include_router(auth_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# Commitment CRUD
# ---------------------------------------------------------------------------

@app.get("/commitments", response_model=list[CommitmentOut])
def list_commitments(db: Session = Depends(get_db)):
    return db.query(Commitment).order_by(Commitment.created_at.desc()).all()


@app.get("/commitments/{commitment_id}", response_model=CommitmentOut)
def get_commitment(commitment_id: int, db: Session = Depends(get_db)):
    commitment = db.query(Commitment).filter(Commitment.id == commitment_id).first()
    if not commitment:
        raise HTTPException(status_code=404, detail="Commitment not found")
    return commitment


@app.patch("/commitments/{commitment_id}", response_model=CommitmentOut)
async def update_commitment(commitment_id: int, payload: CommitmentUpdate, db: Session = Depends(get_db)):
    commitment = db.query(Commitment).filter(Commitment.id == commitment_id).first()
    if not commitment:
        raise HTTPException(status_code=404, detail="Commitment not found")

    changes = payload.model_dump(exclude_unset=True)
    old_status = commitment.status
    old_clash_group_id = commitment.clash_group_id
    old_due_date = commitment.due_date

    if "owner" in changes and changes["owner"] != commitment.owner:
        release_finished_outcome(db, commitment)

    for field, value in changes.items():
        setattr(commitment, field, value)

    if "status" in changes:
        if changes["status"] == "done" and old_status != CommitmentStatus.done:
            commitment.completed_at = datetime.utcnow()
        elif changes["status"] != "done":
            commitment.completed_at = None

    db.add(AuditLog(commitment_id=commitment.id, event="edited", detail=str(changes)))
    db.commit()
    db.refresh(commitment)

    # Marking done cleans up any pending draft in Gmail
    if "status" in changes and changes["status"] == "done" and commitment.gmail_draft_id:
        draft_id_to_remove = commitment.gmail_draft_id

        def _delete_draft():
            from app.ingestion.gmail_connector import GmailConnector
            connector = GmailConnector(max_results=1)
            connector.delete_draft(draft_id_to_remove)

        try:
            await asyncio.to_thread(_delete_draft)
        except Exception as e:
            print(f"Failed to delete draft {draft_id_to_remove}: {e}")

        commitment.gmail_draft_id = None
        db.commit()
        db.refresh(commitment)

    # A finished or cancelled meeting no longer needs its availability email
    if "status" in changes and changes["status"] in ("done", "cancelled") and commitment.meeting_draft_id:
        meeting_draft = commitment.meeting_draft_id
        try:
            await asyncio.to_thread(lambda: _gmail().delete_draft(meeting_draft))
        except Exception as e:
            print(f"Failed to delete meeting draft {meeting_draft}: {e}")
        commitment.meeting_draft_id = None
        db.commit()
        db.refresh(commitment)

    # A meeting that moved: keep its calendar event / availability email in step
    if meetings_enabled() and commitment.is_meeting and "due_date" in changes and commitment.due_date != old_due_date:
        try:
            await asyncio.to_thread(_sync_meeting_time, db, commitment)
        except Exception as e:
            print(f"Couldn't update the meeting for commitment {commitment.id}: {e}")
        db.refresh(commitment)

    # Recompute clashes now that this commitment changed
    clash_changes = detect_clashes(db)
    db.refresh(commitment)
    await _handle_new_clashes(db, clash_changes)
    await _handle_resolved_clashes(db, clash_changes)

    # Rescheduling a clashing, non-important commitment drafts an apology
    owner_name_env = os.environ.get("OWNER_NAME", "").strip().lower()

    if (
        "due_date" in changes
        and old_clash_group_id is not None
        and not commitment.is_important
        and commitment.owner.strip().lower() == owner_name_env
        and commitment.beneficiary_email
    ):
        def _draft_apology():
            from app.ingestion.gmail_connector import GmailConnector
            body = generate_apology_body(commitment.description, old_due_date, commitment.due_date)
            if not body:
                return None
            connector = GmailConnector(max_results=1)
            return connector.create_draft(
                commitment.beneficiary_email,
                f"Updated timeline: {commitment.description}",
                body,
            )

        try:
            draft_id = await asyncio.to_thread(_draft_apology)
            if draft_id:
                commitment.gmail_draft_id = draft_id
                commitment.last_nudged_at = datetime.utcnow()
                db.add(AuditLog(
                    commitment_id=commitment.id,
                    event="reschedule_apology_drafted",
                    detail=f"Drafted apology for moving due date from {old_due_date} to {commitment.due_date}",
                ))
                db.commit()
                db.refresh(commitment)
                await manager.broadcast({"type": "nudge_drafted", "commitment_id": commitment.id})
        except Exception as e:
            print(f"Failed to draft reschedule apology: {e}")

    # Re-score now that status / due date / importance / clash may have changed
    await _refresh_priorities(db)
    db.refresh(commitment)

    await manager.broadcast({"type": "commitment_updated", "commitment_id": commitment.id})
    for c in clash_changes:
        await manager.broadcast({"type": "commitment_updated", "commitment_id": c["commitment_id"]})

    return commitment


@app.delete("/commitments/{commitment_id}")
async def delete_commitment(commitment_id: int, db: Session = Depends(get_db)):
    commitment = db.query(Commitment).filter(Commitment.id == commitment_id).first()
    if not commitment:
        raise HTTPException(status_code=404, detail="Commitment not found")

    deleted_id = await asyncio.to_thread(_delete_commitment_sync, db, commitment)
    await manager.broadcast({"type": "commitment_deleted", "commitment_id": deleted_id})
    return {"deleted": deleted_id}


@app.delete("/commitments/{commitment_id}/draft")
async def delete_commitment_draft(commitment_id: int, db: Session = Depends(get_db)):
    """Deletes just the Gmail draft attached to a commitment (keeps the commitment)."""
    commitment = db.query(Commitment).filter(Commitment.id == commitment_id).first()
    if not commitment:
        raise HTTPException(status_code=404, detail="Commitment not found")
    if not commitment.gmail_draft_id:
        raise HTTPException(status_code=400, detail="No draft associated with this commitment")

    draft_id = commitment.gmail_draft_id

    def _delete():
        from app.ingestion.gmail_connector import GmailConnector
        connector = GmailConnector(max_results=1)
        connector.delete_draft(draft_id)

    try:
        await asyncio.to_thread(_delete)
    except Exception as e:
        print(f"Failed to delete draft {draft_id}: {e}")

    commitment.gmail_draft_id = None
    db.add(AuditLog(commitment_id=commitment.id, event="draft_deleted", detail="Draft manually deleted from frontend"))
    db.commit()

    await manager.broadcast({"type": "commitment_updated", "commitment_id": commitment.id})
    return {"deleted_draft": True}


# ---------------------------------------------------------------------------
# Manual triggers (for testing / on-demand runs)
# ---------------------------------------------------------------------------

@app.post("/ingest/run-now")
async def ingest_now():
    """Sync every enabled source right now."""
    result = await asyncio.to_thread(run_ingestion, 10)
    await _after_ingestion(result)
    return {
        "created": len(result["created"]),
        "auto_verified": len(result["auto_verified"]),
        "already_seen": result["skipped"],
        "deferred": result["deferred"],
    }


class IngestTextPayload(BaseModel):
    text: str
    kind: str = "conversation"       # "conversation" (chat / meeting transcript) or "email"
    title: Optional[str] = None      # e.g. "UI briefing meeting"
    author: Optional[str] = None     # who wrote it, for single-author text
    sent_at: Optional[str] = None    # YYYY-MM-DD the text was written / the meeting was held (default: today)


@app.post("/ingest/text")
async def ingest_text(payload: IngestTextPayload):
    """Paste in a Teams chat, meeting notes or a transcript and have it tracked
    exactly like any other source."""
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is empty")
    if payload.kind not in ("conversation", "email"):
        raise HTTPException(status_code=400, detail="kind must be 'conversation' or 'email'")

    sent_at = payload.sent_at or date.today().isoformat()
    try:
        date.fromisoformat(sent_at)
    except ValueError:
        raise HTTPException(status_code=400, detail="sent_at must look like 2026-09-25")

    title = payload.title.strip() if payload.title else ""
    message = RawMessage(
        channel="manual",
        external_id=hashlib.sha256(text.encode("utf-8")).hexdigest()[:24],
        text=(f"{title}\n\n" if title else "") + text,
        author=payload.author.strip() if payload.author and payload.author.strip() else None,
        sent_at=sent_at,
        kind=payload.kind,
    )

    result = await asyncio.to_thread(run_ingestion, 10, [InMemoryConnector("manual", [message])])
    if result["deferred"]:
        raise HTTPException(
            status_code=503,
            detail="The AI service is unavailable right now - nothing was saved. Try again shortly.",
        )
    await _after_ingestion(result)
    return {
        "created": result["created"],
        "auto_verified": len(result["auto_verified"]),
        "already_ingested": result["skipped"] > 0,
    }


@app.get("/sources")
def sources():
    """Which sources are switched on, and where to drop transcript files."""
    enabled = enabled_source_names()
    info = {"enabled": enabled}
    if "file" in enabled:
        from app.ingestion.file_connector import FileDropConnector
        connector = FileDropConnector()
        info["file_drop"] = {"folder": str(connector.drop_dir), "waiting": connector.pending_count()}
    return info


def _require_meetings():
    if not meetings_enabled():
        raise HTTPException(
            status_code=400,
            detail="Calendar scheduling is switched off. Set MEETINGS_ENABLED=true in .env and restart.",
        )


@app.post("/commitments/{commitment_id}/schedule")
async def schedule_commitment_meeting(commitment_id: int, force: bool = False, db: Session = Depends(get_db)):
    """Adds the meeting to your Google Calendar (with a Meet link, no guests).
    409 + reason 'busy' if you have a clash; call again with ?force=true to override."""
    _require_meetings()
    commitment = _commitment_or_404(db, commitment_id)
    try:
        result = await asyncio.to_thread(
            lambda: meeting_agent.schedule_meeting(db, GoogleCalendar(), commitment, force=force)
        )
    except meeting_agent.Busy as e:
        await manager.broadcast({"type": "commitment_updated", "commitment_id": commitment_id})
        raise HTTPException(status_code=409, detail={
            "reason": "busy", "message": str(e), "alternatives": e.alternatives,
        })
    except meeting_agent.AlreadyScheduled as e:
        raise HTTPException(status_code=409, detail=str(e))
    except meeting_agent.MeetingError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except CalendarUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Couldn't reach Google Calendar: {e}")

    await manager.broadcast({"type": "commitment_updated", "commitment_id": commitment_id})
    return result


@app.delete("/commitments/{commitment_id}/event")
async def unschedule_commitment_meeting(commitment_id: int, db: Session = Depends(get_db)):
    """Removes the meeting from your calendar (the commitment stays)."""
    _require_meetings()
    commitment = _commitment_or_404(db, commitment_id)
    try:
        await asyncio.to_thread(lambda: meeting_agent.unschedule_meeting(db, GoogleCalendar(), commitment))
    except meeting_agent.MeetingError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except CalendarUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Couldn't reach Google Calendar: {e}")

    await manager.broadcast({"type": "commitment_updated", "commitment_id": commitment_id})
    return {"removed": True}


@app.delete("/commitments/{commitment_id}/meeting-draft")
async def delete_commitment_meeting_draft(commitment_id: int, db: Session = Depends(get_db)):
    """Deletes just the availability email draft (the commitment and any calendar event stay)."""
    commitment = _commitment_or_404(db, commitment_id)
    try:
        await asyncio.to_thread(lambda: meeting_agent.delete_meeting_draft(db, _gmail(), commitment))
    except meeting_agent.MeetingError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await manager.broadcast({"type": "commitment_updated", "commitment_id": commitment_id})
    return {"deleted_draft": True}


@app.post("/meetings/run-now")
async def meetings_now():
    """Check any newly detected meetings right now instead of waiting for the next cycle."""
    _require_meetings()
    try:
        results = await asyncio.to_thread(_run_meeting_checks, 20)
    except CalendarUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Couldn't reach Google Calendar: {e}")
    await _announce_meeting_checks(results)
    return {"checked": results}


@app.get("/meetings/status")
def meetings_status():
    s = meeting_agent.settings()
    return {
        "enabled": meetings_enabled(),
        "meeting_minutes": s["minutes"],
        "work_hours": f"{s['work_start']}-{s['work_end']}",
        "skip_weekends": s["skip_weekends"],
        "meet_link": s["with_meet"],
    }


@app.post("/merge/run-now")
async def merge_now(db: Session = Depends(get_db)):
    merges = find_and_merge_duplicates(db)
    for m in merges:
        await manager.broadcast({"type": "commitment_deleted", "commitment_id": m["removed_id"]})
        await manager.broadcast({"type": "commitment_updated", "commitment_id": m["kept_id"]})
    return {"merged": len(merges), "details": merges}


@app.post("/nudges/run-now")
async def nudges_now(db: Session = Depends(get_db)):
    from app.ingestion.gmail_connector import GmailConnector
    connector = GmailConnector(max_results=1)
    drafted = find_and_draft_nudges(db, connector)
    for commitment_id in drafted:
        await manager.broadcast({"type": "nudge_drafted", "commitment_id": commitment_id})
    return {"drafted": len(drafted), "commitment_ids": drafted}


@app.post("/clashes/run-now")
async def clashes_now(db: Session = Depends(get_db)):
    changes = detect_clashes(db)
    for c in changes:
        await manager.broadcast({"type": "commitment_updated", "commitment_id": c["commitment_id"]})
    await _handle_new_clashes(db, changes)
    await _handle_resolved_clashes(db, changes)
    await _refresh_priorities(db)
    return {"changed": len(changes), "details": changes}


@app.post("/priorities/run-now")
async def priorities_now(db: Session = Depends(get_db)):
    changed = await _refresh_priorities(db)
    top = (
        db.query(Commitment)
        .filter(Commitment.priority_score > 0)
        .order_by(Commitment.priority_score.desc())
        .limit(5)
        .all()
    )
    return {
        "rescored": len(changed),
        "top": [
            {"id": c.id, "description": c.description, "score": c.priority_score, "why": c.priority_reason}
            for c in top
        ],
    }


# ---------------------------------------------------------------------------
# Activity feed
# ---------------------------------------------------------------------------

@app.get("/people")
def people(db: Session = Depends(get_db)):
    """Per-person reliability for the Overview 'People' card."""
    return people_summary(db)


@app.get("/activity")
def recent_activity(db: Session = Depends(get_db)):
    events = (
        db.query(AuditLog)
        .order_by(AuditLog.timestamp.desc())
        .limit(20)
        .all()
    )
    return [
        {
            "id": e.id,
            "commitment_id": e.commitment_id,
            "event": e.event,
            "detail": e.detail,
            "timestamp": e.timestamp.isoformat(),
        }
        for e in events
    ]