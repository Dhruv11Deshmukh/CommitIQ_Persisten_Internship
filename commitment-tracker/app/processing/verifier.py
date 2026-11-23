"""
Layer 2: Processing & Analytics - Self-verification engine.

Checks whether a new message reports that an EXISTING open commitment has
been completed (e.g. "Sent the file over", "Done with the report", "Submitted
it just now") and returns that commitment's id if so.

This is a second, smaller Gemini call made only when the message's author
has open commitments of their own - keeps the extra cost/latency limited
to cases where it's actually relevant.
"""
import os
import json
import time
from typing import List, Optional, TypedDict
from pydantic import BaseModel
from dotenv import load_dotenv
from google import genai
from google.genai.errors import ServerError, ClientError

load_dotenv()

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

VERIFICATION_PROMPT = """You check whether a new message reports that an \
existing open task/commitment has been completed.

Below is a list of open commitments belonging to {owner}, each with an id \
and description. Then there is a new message written by {owner}. Decide if \
the new message clearly indicates that ANY of these specific commitments \
has now been completed - phrases like "sent it", "done", "finished the \
report", "just submitted it", "all set now".

Only match if the message clearly refers to that specific task. If the \
message is ambiguous, unrelated, or you're not confident, return null - do \
not guess.

Open commitments belonging to {owner}:
{commitments_list}

New message from {owner}:
{text}

Return ONLY valid JSON, no markdown fences, no commentary:
{{"completed_commitment_id": <id or null>}}
"""


class VerificationResult(BaseModel):
    completed_commitment_id: Optional[int] = None


def check_for_completion(owner: str, open_commitments, text: str):
    if not open_commitments:
        return None

    commitments_list = "\n".join(f"- id={c['id']}: {c['description']}" for c in open_commitments)
    prompt = VERIFICATION_PROMPT.format(owner=owner, commitments_list=commitments_list, text=text[:3000])

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
        print(f"Verification check failed: {last_error}")
        return None

    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        data = json.loads(raw)
        result = VerificationResult(**data)
        return result.completed_commitment_id
    except (json.JSONDecodeError, TypeError):
        return None