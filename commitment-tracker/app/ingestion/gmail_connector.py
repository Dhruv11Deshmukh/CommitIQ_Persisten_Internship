"""
Layer 1: Ingestion - Gmail connector.

Fetches from BOTH inbox (commitments others made to you) and sent mail
(commitments you made to others). For short replies (e.g. just "Yes"),
pulls the previous message in the thread as context. Also supports creating
and deleting Gmail drafts (for nudges/apologies) - never sending.

Sign-in is shared with Google Calendar - see app/integrations/google_auth.py.
"""
import base64
from email.mime.text import MIMEText
from email.utils import parseaddr, parsedate_to_datetime
from typing import List, Optional

from googleapiclient.discovery import build

from app.ingestion.base import BaseConnector, RawMessage
from app.integrations.google_auth import get_credentials

LABELS_TO_FETCH = ["INBOX", "SENT"]

SHORT_REPLY_WORD_LIMIT = 8


def _extract_body(payload) -> str:
    if payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")

    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")

    for part in payload.get("parts", []):
        if part.get("parts"):
            result = _extract_body(part)
            if result:
                return result

    return ""


def _is_short_reply(text: str) -> bool:
    return len(text.strip().split()) <= SHORT_REPLY_WORD_LIMIT


class GmailConnector(BaseConnector):
    channel_name = "gmail"

    def __init__(self, max_results: int = 10):
        self.max_results = max_results
        creds = get_credentials()
        self.service = build("gmail", "v1", credentials=creds)

    def _get_previous_message_in_thread(self, thread_id: str, current_msg_id: str) -> Optional[str]:
        if not thread_id:
            return None
        try:
            thread = self.service.users().threads().get(
                userId="me", id=thread_id, format="full"
            ).execute()
        except Exception:
            return None

        thread_messages = thread.get("messages", [])
        idx = next((i for i, m in enumerate(thread_messages) if m["id"] == current_msg_id), None)
        if idx is None or idx == 0:
            return None

        prev = thread_messages[idx - 1]
        headers = {h["name"]: h["value"] for h in prev["payload"].get("headers", [])}
        sender = headers.get("From", "unknown")
        subject = headers.get("Subject", "")
        body = _extract_body(prev["payload"])

        return f"From: {sender}\nSubject: {subject}\n\n{body}".strip()

    def _fetch_label(self, label: str, direction: str) -> List[RawMessage]:
        results = self.service.users().messages().list(
            userId="me", maxResults=self.max_results, labelIds=[label]
        ).execute()

        messages = results.get("messages", [])
        raw_messages = []

        for msg_meta in messages:
            msg = self.service.users().messages().get(
                userId="me", id=msg_meta["id"], format="full"
            ).execute()

            headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
            sender = headers.get("From", "unknown")
            recipient = headers.get("To", "")
            subject = headers.get("Subject", "")
            date_header = headers.get("Date", "")
            body = _extract_body(msg["payload"])

            sent_at = None
            if date_header:
                try:
                    sent_at = parsedate_to_datetime(date_header).date().isoformat()
                except (ValueError, TypeError):
                    sent_at = None

            context_block = ""
            if _is_short_reply(body):
                prev_text = self._get_previous_message_in_thread(msg.get("threadId"), msg["id"])
                if prev_text:
                    context_block = (
                        f"[PREVIOUS MESSAGE IN THREAD - this is the request being replied to]\n"
                        f"{prev_text}\n\n"
                        f"[THIS MESSAGE - a reply to the above]\n"
                    )

            author_name, author_email = parseaddr(sender)
            author = author_name if author_name else author_email

            first_recipient = recipient.split(",")[0] if recipient else ""
            _, recipient_email = parseaddr(first_recipient)

            full_text = (
                f"{context_block}From: {sender}\nTo: {recipient}\nSubject: {subject}\n\n{body}"
            ).strip()

            raw_messages.append(RawMessage(
                channel=self.channel_name,
                external_id=msg["id"],
                text=full_text,
                author=author,
                author_email=author_email,
                recipient_email=recipient_email,
                direction=direction,
                sent_at=sent_at,
                kind="email",
            ))

        return raw_messages

    def fetch_new_messages(self) -> List[RawMessage]:
        all_messages = []
        seen_ids = set()

        direction_map = {"INBOX": "received", "SENT": "sent"}

        for label in LABELS_TO_FETCH:
            for msg in self._fetch_label(label, direction_map[label]):
                if msg.external_id not in seen_ids:
                    seen_ids.add(msg.external_id)
                    all_messages.append(msg)

        return all_messages

    def create_draft(self, to_email: str, subject: str, body: str) -> str:
        """Creates a draft in Gmail (never sends). Returns the draft id."""
        message = MIMEText(body)
        if to_email:
            message["to"] = to_email
        message["subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

        draft = self.service.users().drafts().create(
            userId="me", body={"message": {"raw": raw}}
        ).execute()
        return draft.get("id")

    def delete_draft(self, draft_id: str):
        """Deletes a Gmail draft. Safe to call even if it was already sent
        or deleted manually - failures here are logged, not raised."""
        try:
            self.service.users().drafts().delete(userId="me", id=draft_id).execute()
        except Exception as e:
            print(f"Could not delete draft {draft_id}: {e}")


if __name__ == "__main__":
    connector = GmailConnector(max_results=5)
    for m in connector.fetch_new_messages():
        print("---")
        print(f"[{m.direction}] From: {m.author} <{m.author_email}>  To email: {m.recipient_email}")
        print(m.text[:400])