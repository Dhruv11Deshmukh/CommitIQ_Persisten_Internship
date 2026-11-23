"""
Layer 1: Ingestion - the contract every source implements.

A source (Gmail, a folder of transcripts, pasted text, and later Teams) only
has to do two things:

  1. fetch_new_messages()  -> a list of RawMessage
  2. acknowledge(message)  -> optional clean-up once a message is safely stored

Everything after that (extraction, clashes, nudges, priority, trust) is
identical for every source, so adding one never touches the processing layer.
"""
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class RawMessage:
    channel: str                         # "gmail" | "file" | "manual" | later "teams"
    external_id: Optional[str]           # unique WITHIN the channel; used to avoid re-ingesting
    text: str
    author: Optional[str] = None         # who wrote it (emails). None for multi-speaker text.
    author_email: Optional[str] = None
    recipient_email: Optional[str] = None
    direction: Optional[str] = None      # "received" | "sent" (email only)
    sent_at: Optional[str] = None        # ISO date the content was written / the meeting was held
    kind: str = "email"                  # "email" | "conversation" (chat or meeting transcript)


class BaseConnector:
    channel_name: str = "base"

    def fetch_new_messages(self) -> List[RawMessage]:
        raise NotImplementedError

    def acknowledge(self, message: RawMessage) -> None:
        """Called by the pipeline once a message is safely stored, or was
        already stored earlier. Default: nothing to do."""
        return None


class InMemoryConnector(BaseConnector):
    """Serves a fixed list of messages. Used for pasted text and in tests."""

    def __init__(self, channel_name: str, messages: List[RawMessage]):
        self.channel_name = channel_name
        self._messages = list(messages)

    def fetch_new_messages(self) -> List[RawMessage]:
        return list(self._messages)