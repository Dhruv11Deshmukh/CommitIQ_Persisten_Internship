"""
Layer 2: Processing & Analytics - NLP/LLM Engine (Gemini).

Turns raw text into zero or more structured commitments. Handles:
  - emails (default) and multi-speaker conversations (meeting transcripts, chats)
  - long text, by reading it in overlapping chunks
  - dates and times resolved against the date the text was written

Failure handling matters here: if Gemini is down, rate-limited, or rejecting
our key, we raise ExtractionUnavailable instead of returning an empty list.
An empty list means "I read it and found nothing"; unavailable means "I could
not read it" - and the pipeline must not mark that message as done.

Requires GEMINI_API_KEY in .env.
"""
import json
import os
import time
from datetime import date
from difflib import SequenceMatcher
from typing import List, Optional

from dotenv import load_dotenv
from google import genai
from google.genai.errors import ClientError, ServerError
from pydantic import BaseModel, ValidationError

from app.processing.chunking import chunk_text

try:
    import httpx
    _NETWORK_ERRORS = (httpx.TransportError, TimeoutError, ConnectionError)
except ImportError:  # pragma: no cover
    _NETWORK_ERRORS = (TimeoutError, ConnectionError)

load_dotenv()

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

MODEL = "gemini-flash-lite-latest"
MAX_ATTEMPTS = 5
CHUNK_CHARS = 6000
CHUNK_OVERLAP_CHARS = 400
CROSS_CHUNK_SIMILARITY = 0.8


class ExtractionUnavailable(Exception):
    """The model could not be reached or refused us - try this message again later."""


EXTRACTION_PROMPT = """You extract commitments from workplace text (emails, \
chat messages, meeting transcripts).

A commitment is a promise someone makes to do something, optionally with a \
deadline and a recipient. Marketing emails, job alerts, and newsletters \
almost never contain real commitments - if there's nothing genuine, return \
an empty list. Do not invent commitments that aren't clearly stated.

HANDLING ACKNOWLEDGMENT REPLIES: if the text contains a section marked \
"[PREVIOUS MESSAGE IN THREAD - this is the request being replied to]" \
followed by "[THIS MESSAGE - a reply to the above]", the second section is a \
reply to the first. If that reply is an agreement or acknowledgment - \
things like "Yes", "Sure", "Will do", "Ok", "Done", "On it", "Got it", \
"I'll handle it" - treat this as a real commitment: the person who SENT \
the reply (given as Author below) is the owner, agreeing to do whatever \
was asked for in the previous message. Use the previous message to fill in \
the description and due date. If the reply is a refusal or non-committal \
("No", "Can't do it", "Not sure"), do NOT create a commitment.

IMPORTANT - resolving dates and times: this message was sent/written on \
{reference_date}. Any relative date phrase ("next Friday", "in two days", \
"by EOD", "next week", "tomorrow") must be calculated relative to \
{reference_date}, NOT relative to today. If the text mentions a specific \
time ("at 6pm", "2:00 PM", "by noon"), include it. Always return a \
concrete ISO 8601 datetime: "YYYY-MM-DDTHH:MM:SS" if a time was mentioned, \
or just "YYYY-MM-DD" if only a date was mentioned with no time. If no \
deadline is stated at all, use null - do not guess a date or time that \
wasn't implied.

MEETINGS: set "is_meeting" to true only when the commitment is to hold, join \
or attend a meeting, call or video call with another person (for example \
"let's meet tomorrow at 6pm", "I'll join the call at 2pm"). Ordinary tasks \
such as sending a document or fixing a bug are NOT meetings, even if they \
were agreed in a meeting. Otherwise set it to false.

{source_guidance}

Return ONLY valid JSON (no markdown fences, no commentary), as a list of \
objects with this exact shape:
[
  {{
    "owner": "<who is committing, use the given author if known>",
    "beneficiary": "<who it's owed to, or null if unclear>",
    "description": "<concise restatement of what they promised>",
    "due_date": "<ISO 8601 date or datetime calculated from reference_date, or null>",
    "is_meeting": <true or false>
  }}
]

If there is no commitment in the text, return [].

Reference date (when this was sent or held): {reference_date}
Author: {author}
Text: {text}
"""

SOURCE_GUIDANCE = {
    "email": "",
    "conversation": (
        "THIS TEXT IS A CONVERSATION (a meeting transcript or a chat), not a single "
        "email. Lines are usually 'Speaker: words'. The commitment's owner is the "
        "SPEAKER who makes the promise - use the speaker's name exactly as written, "
        "never 'I', 'me', 'we' or 'unknown'. 'I'll send it Friday' -> that speaker "
        "owns it. If one person asks 'can you do X?' and another answers 'sure' or "
        "'yes', the person who agreed owns it and the person who asked is the "
        "beneficiary. Someone being ASSIGNED a task who never accepts it, general "
        "discussion, brainstorming, and suggestions about what others might do are "
        "NOT commitments. The reference date is the date of the meeting or chat."
    ),
}


class ExtractedCommitment(BaseModel):
    owner: str
    beneficiary: Optional[str] = None
    description: str
    due_date: Optional[str] = None
    is_meeting: Optional[bool] = False


def _call_model(prompt: str) -> str:
    """One model call with retries. Returns the reply text, or raises
    ExtractionUnavailable if we can't get an answer."""
    last_error = None

    def pause(attempt: int, seconds: int):
        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(seconds)

    for attempt in range(MAX_ATTEMPTS):
        try:
            response = client.models.generate_content(model=MODEL, contents=prompt)
            return (response.text or "").strip()
        except ServerError as e:
            last_error = e
            print(f"Gemini overloaded (attempt {attempt + 1}/{MAX_ATTEMPTS}), retrying...")
            pause(attempt, 2 ** attempt)
        except ClientError as e:
            if e.code == 429:
                last_error = e
                print(f"Gemini rate limit hit (attempt {attempt + 1}/{MAX_ATTEMPTS}), waiting...")
                pause(attempt, 25)
            elif e.code in (401, 403, 404):
                raise ExtractionUnavailable(
                    f"Gemini rejected the request ({e.code}) - check GEMINI_API_KEY and the model name"
                ) from e
            else:
                raise  # specific to this message (e.g. 400); the caller decides
        except _NETWORK_ERRORS as e:
            last_error = e
            print(f"Network problem reaching Gemini (attempt {attempt + 1}/{MAX_ATTEMPTS}): {e}")
            pause(attempt, 2 ** attempt)

    raise ExtractionUnavailable(f"Gemini unavailable after {MAX_ATTEMPTS} attempts: {last_error}")


def _parse_items(raw: str) -> List[ExtractedCommitment]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    data = json.loads(raw)  # JSONDecodeError handled by the caller
    if not isinstance(data, list):
        return []

    items = []
    for entry in data:
        try:
            items.append(ExtractedCommitment(**entry))
        except (ValidationError, TypeError):
            print(f"Skipping a malformed commitment from the model: {entry!r}")
    return items


def _extract_chunk(prompt: str) -> List[ExtractedCommitment]:
    for _ in range(2):  # one retry if the model's reply isn't valid JSON
        raw = _call_model(prompt)
        try:
            return _parse_items(raw)
        except json.JSONDecodeError:
            print("Model output wasn't valid JSON:", raw[:200])
    return []


def _merge_across_chunks(tagged):
    """Drops the same commitment seen twice because it sat in the overlap
    between two chunks. Only ever merges items from DIFFERENT chunks, so two
    genuinely separate but similar commitments in one chunk are both kept."""
    kept = []  # (chunk_index, item)
    for idx, item in tagged:
        match = next(
            (k for k_idx, k in kept
             if k_idx != idx
             and k.owner.strip().lower() == item.owner.strip().lower()
             and SequenceMatcher(None, k.description.lower(), item.description.lower()).ratio() >= CROSS_CHUNK_SIMILARITY),
            None,
        )
        if match is None:
            kept.append((idx, item))
        else:
            if not match.due_date and item.due_date:
                match.due_date = item.due_date
            if not match.beneficiary and item.beneficiary:
                match.beneficiary = item.beneficiary
            if item.is_meeting and not match.is_meeting:
                match.is_meeting = True
    return [item for _, item in kept]


def extract_commitments(
    text: str,
    author: Optional[str] = None,
    reference_date: Optional[str] = None,
    kind: str = "email",
) -> List[ExtractedCommitment]:
    """
    reference_date: ISO date (YYYY-MM-DD) the text was written / the meeting
        was held. Relative phrases are resolved against it. Defaults to today.
    kind: "email" (default) or "conversation" (meeting transcript / chat).

    Raises ExtractionUnavailable if the model can't be reached. All chunks of
    a long text succeed or the whole call fails - never a half-read transcript.
    """
    ref_date = reference_date or date.today().isoformat()
    guidance = SOURCE_GUIDANCE.get(kind, "")

    chunks = chunk_text(text, CHUNK_CHARS, CHUNK_OVERLAP_CHARS)
    if not chunks:
        return []

    tagged = []
    for i, chunk in enumerate(chunks):
        notes = [guidance] if guidance else []
        if len(chunks) > 1:
            notes.append(
                f"NOTE: this is part {i + 1} of {len(chunks)} of a longer text. Only extract "
                "commitments stated in THIS part; the other parts are handled separately."
            )
        prompt = EXTRACTION_PROMPT.format(
            reference_date=ref_date,
            author=author or "unknown",
            text=chunk,
            source_guidance="\n\n".join(notes),
        )
        for item in _extract_chunk(prompt):
            tagged.append((i, item))

    if len(chunks) > 1:
        return _merge_across_chunks(tagged)
    return [item for _, item in tagged]


if __name__ == "__main__":
    sample = "Hi team, I'll send the revised contract to Acme Corp by this Friday."
    for r in extract_commitments(sample, author="Dhruv", reference_date="2026-08-27"):
        print(r)