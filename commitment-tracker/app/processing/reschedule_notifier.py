"""
Layer 4-ish agent: Reschedule apology drafts.

When a commitment that was flagged as clashing gets rescheduled (due date
changed) and isn't marked important, drafts a short apology explaining the
new date. Never auto-sent - saved as a Gmail draft for review, same as
the follow-up nudge agent.
"""
import os
import time
from dotenv import load_dotenv
from google import genai
from google.genai.errors import ServerError, ClientError

load_dotenv()

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

APOLOGY_PROMPT = """Write a short, polite email explaining that a commitment \
had to be rescheduled because of a scheduling conflict on the original date.

Task: {description}
Original due date: {old_due_date}
New due date: {new_due_date}

Tone: apologetic but brief and professional - something like "I'm sorry, \
I'm already committed to something else that day, so I've moved this to \
{new_due_date}." Keep it under 60 words. Return ONLY the email body text - \
no subject line, no markdown, no commentary.
"""

CONFLICT_NOTICE_PROMPT = """Write a short, polite email letting the \
recipient know that a scheduling conflict has come up for the task below,
and you'll follow up soon with a revised timeline. No new date is decided
yet - just a heads-up.

Task: {description}
Original due date: {due_date}

Tone: apologetic, brief, professional - something like "I've run into a \
scheduling conflict for this date and wanted to give you a heads up - I'll \
follow up shortly with a revised timeline." Keep it under 60 words. Return \
ONLY the email body text - no subject line, no markdown, no commentary.
"""


def generate_apology_body(description: str, old_due_date, new_due_date):
    prompt = APOLOGY_PROMPT.format(
        description=description,
        old_due_date=old_due_date.strftime("%B %d, %Y") if old_due_date else "the original date",
        new_due_date=new_due_date.strftime("%B %d, %Y") if new_due_date else "a new date",
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
        print(f"Apology draft generation failed: {last_error}")
        return None

    return response.text.strip()


def generate_conflict_notice_body(description: str, due_date):
    prompt = CONFLICT_NOTICE_PROMPT.format(
        description=description,
        due_date=due_date.strftime("%B %d, %Y") if due_date else "the scheduled date",
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
        print(f"Conflict notice draft generation failed: {last_error}")
        return None

    return response.text.strip()