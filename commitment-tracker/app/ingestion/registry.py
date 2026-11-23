"""
Layer 1: Ingestion - which sources are switched on.

Set ENABLED_SOURCES in .env, e.g.  ENABLED_SOURCES=gmail,file
Adding a new source later (Teams, Slack...) = one more branch here.

Each source is built independently, so one that fails to start (say Gmail's
login expired) never stops the others from working.
"""
import os
from typing import List, Optional

DEFAULT_SOURCES = "gmail,file"


def enabled_source_names() -> List[str]:
    raw = os.environ.get("ENABLED_SOURCES", DEFAULT_SOURCES)
    names = []
    for name in raw.split(","):
        name = name.strip().lower()
        if name and name not in names:
            names.append(name)
    return names


def build_connectors(names: Optional[List[str]] = None, max_emails: int = 10):
    connectors = []
    for name in (names if names is not None else enabled_source_names()):
        try:
            if name == "gmail":
                from app.ingestion.gmail_connector import GmailConnector
                connectors.append(GmailConnector(max_results=max_emails))
            elif name == "file":
                from app.ingestion.file_connector import FileDropConnector
                connectors.append(FileDropConnector())
            else:
                print(f"[sources] unknown source '{name}' in ENABLED_SOURCES - skipping")
        except Exception as e:
            print(f"[sources] could not start '{name}': {e}")
    return connectors