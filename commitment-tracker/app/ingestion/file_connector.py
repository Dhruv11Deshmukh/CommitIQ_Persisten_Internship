"""
Layer 1: Ingestion - file-drop connector.

Watches a folder. Drop a meeting transcript (.vtt / .docx / .txt / .md) or a
chat export in it and it's ingested within seconds, then moved to
`processed/` (or `failed/` if it can't be read). Works with ANY source that
can produce a file - including Teams transcripts you can download.

Tip: put the meeting date in the filename ("2026-09-25 UI briefing.vtt").
Relative dates in the conversation ("by Friday") are resolved against it. If
there's no date in the name, the file's modified date is used, which is the
DOWNLOAD date, not the meeting date, so naming files is worth it.

Re-dropping identical content is harmless: files are de-duplicated by a hash
of their content, not their name.
"""
import hashlib
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from app.ingestion.base import BaseConnector, RawMessage
from app.ingestion.transcripts import read_transcript_file

SUPPORTED_EXTENSIONS = {".vtt", ".txt", ".md", ".docx"}

_DATE_IN_NAME = re.compile(r"(?<!\d)(20\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)")


def date_from_filename(name: str):
    m = _DATE_IN_NAME.search(name)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date().isoformat()
    except ValueError:
        return None


class FileDropConnector(BaseConnector):
    channel_name = "file"

    def __init__(self, drop_dir=None, settle_seconds=None):
        self.drop_dir = Path(drop_dir or os.environ.get("FILE_DROP_DIR", "./drop")).resolve()
        self.processed_dir = self.drop_dir / "processed"
        self.failed_dir = self.drop_dir / "failed"
        self.settle_seconds = (
            float(os.environ.get("FILE_SETTLE_SECONDS", "2")) if settle_seconds is None else settle_seconds
        )
        for d in (self.drop_dir, self.processed_dir, self.failed_dir):
            d.mkdir(parents=True, exist_ok=True)
        self._paths: Dict[str, List[Path]] = {}  # content hash -> files carrying that content

    # -- discovery ---------------------------------------------------------

    def _candidates(self) -> List[Path]:
        return [
            p for p in sorted(self.drop_dir.iterdir())
            if p.is_file()
            and p.suffix.lower() in SUPPORTED_EXTENSIONS
            and not p.name.startswith(("~$", "."))
        ]

    def pending_count(self) -> int:
        return len(self._candidates())

    def has_pending(self) -> bool:
        return self.pending_count() > 0

    def _settled(self, path: Path) -> bool:
        """False while a file is still being written/copied."""
        try:
            if time.time() - path.stat().st_mtime < self.settle_seconds:
                return False
            size_before = path.stat().st_size
            time.sleep(0.2)
            return path.stat().st_size == size_before
        except OSError:
            return False

    # -- connector contract ------------------------------------------------

    def fetch_new_messages(self) -> List[RawMessage]:
        messages: List[RawMessage] = []
        by_digest: Dict[str, RawMessage] = {}
        self._paths = {}

        for path in self._candidates():
            if not self._settled(path):
                continue
            try:
                body = read_transcript_file(path).strip()
            except PermissionError:
                continue  # locked by another program; try again next poll
            except Exception as e:
                print(f"[file] could not read {path.name}: {e} - moving to failed/")
                self._move(path, self.failed_dir)
                continue

            if not body:
                print(f"[file] {path.name} is empty - moving to failed/")
                self._move(path, self.failed_dir)
                continue

            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:24]
            self._paths.setdefault(digest, []).append(path)
            if digest in by_digest:
                continue  # identical content in a second file: one message, both files get cleaned up

            held_on = date_from_filename(path.name) or datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()
            msg = RawMessage(
                channel=self.channel_name,
                external_id=digest,
                text=f"Meeting transcript or chat file: {path.name}\n\n{body}",
                author=None,
                sent_at=held_on,
                kind="conversation",
            )
            by_digest[digest] = msg
            messages.append(msg)

        return messages

    def acknowledge(self, message: RawMessage) -> None:
        for path in self._paths.pop(message.external_id, []):
            self._move(path, self.processed_dir)

    # -- helpers -----------------------------------------------------------

    def _move(self, path: Path, dest_dir: Path):
        if not path.exists():
            return
        target = dest_dir / path.name
        n = 1
        while target.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            target = dest_dir / f"{path.stem}.{stamp}-{n}{path.suffix}"
            n += 1
        try:
            shutil.move(str(path), str(target))
        except OSError as e:
            print(f"[file] could not move {path.name}: {e}")