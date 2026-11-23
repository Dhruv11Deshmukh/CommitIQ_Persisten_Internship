"""
Layer 4-ish agent: Auto-drafted follow-up nudges.

For commitments that are overdue or due within the next 24 hours, drafts a
short follow-up email and saves it to Gmail Drafts - NEVER auto-sent, you
always review and send manually.

Two cases, based on who owns the commitment:
- Owned by YOU (something you promised someone else) -> drafts a personal
  reminder-to-self.
- Owned by someone else (something they promised you) -> drafts a follow-up
  addressed to them, using their captured email address.
"""
import os
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from google import genai
from google.genai.errors import ServerError, ClientError

load_dotenv()

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
OWNER_NAME = os.environ.get("OWNER_NAME", "").strip().lower()

NUDGE_COOLDOWN_HOURS = 24

DRAFT_PROMPT = """Write a short, polite follow-up email about the task below. \
{tone_instruction}

Task: {description}
Due date: {due_date}

Keep it under 80 words. Return ONLY the email body text - no subject line, \
no markdown, no commentary.
"""


def _generate_draft_body(description: str, due_date, is_overdue: bool, self_reminder: bool):
    if self_reminder:
        tone_instruction = "This is a reminder TO YOURSELF about something you still need to do - write it as a quick personal note-to-self."
    elif is_overdue:
        tone_instruction = "This is overdue - write a polite but clear follow-up asking for an update."
    else:
        tone_instruction = "This is due soon - write a friendly heads-up/reminder, not pushy."

    prompt = DRAFT_PROMPT.format(
        tone_instruction=tone_instruction,
        description=description,
        due_date=due_date.strftime("%B %d, %Y") if due_date else "no specific date",
    )

    response = None
    last_error = None
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model="gemini-flash-lite-latest",
                contents=prompt,
            )
            break
        except ServerError as e:
            last_error = e
            time.sleep(2 ** attempt)
        except ClientError as e:
            last_error = e
            if e.code == 429:
                time.sleep(25)
            else:
                raise

    if response is None:
        print(f"Nudge draft generation failed: {last_error}")
        return None

    return response.text.strip()


def find_and_draft_nudges(db, connector):
    """Scans overdue + due-within-24h commitments, drafts a nudge for each
    one not nudged in the last NUDGE_COOLDOWN_HOURS. Returns a list of
    commitment ids that got a new draft, for broadcasting/logging."""
    from app.models import Commitment, CommitmentStatus, AuditLog

    now = datetime.utcnow()          # cooldown + last_nudged_at stay in UTC
    local_now = datetime.now()       # due dates are local wall-clock times
    soon = local_now + timedelta(hours=24)
    cooldown_cutoff = now - timedelta(hours=NUDGE_COOLDOWN_HOURS)

    candidates = (
        db.query(Commitment)
        .filter(Commitment.status.in_([CommitmentStatus.open, CommitmentStatus.at_risk]))
        .filter(Commitment.due_date.isnot(None))
        .filter(Commitment.due_date <= soon)
        .filter((Commitment.last_nudged_at.is_(None)) | (Commitment.last_nudged_at <= cooldown_cutoff))
        .all()
    )

    drafted = []

    for c in candidates:
        is_overdue = c.due_date < local_now
        self_reminder = bool(OWNER_NAME) and c.owner.strip().lower() == OWNER_NAME

        body = _generate_draft_body(c.description, c.due_date, is_overdue, self_reminder)
        if not body:
            continue

        if self_reminder:
            to_email = ""
            subject = f"Reminder: {c.description}"
        else:
            to_email = c.owner_email or ""
            if not to_email:
                continue  # can't address a draft without a recipient
            subject = f"Following up: {c.description}"

        try:
            draft_id = connector.create_draft(to_email, subject, body)
        except Exception as e:
            print(f"Failed to create draft for commitment {c.id}: {e}")
            continue

        c.last_nudged_at = now
        c.gmail_draft_id = draft_id

        db.add(AuditLog(
            commitment_id=c.id,
            event="nudged",
            detail=f"Drafted a {'reminder' if self_reminder else 'follow-up'} email "
                   f"({'overdue' if is_overdue else 'due soon'})",
        ))
        drafted.append(c.id)

    db.commit()
    return drafted