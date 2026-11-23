"""
Turns transcript / chat files into plain "Speaker: words" text.

  .vtt   WebVTT (what Teams meeting transcripts download as)
  .docx  Word documents (read with the standard library - no extra install)
  .txt / .md   used as-is

Files are assumed to be your own downloads (the XML parser is not hardened
against hostile documents).
"""
import html
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

_TIMING = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}\s*-->")
_VOICE_OPEN = re.compile(r"<v(?:\.[^\s>]+)*\s+([^>]+)>", re.IGNORECASE)
_ANY_TAG = re.compile(r"<[^>]+>")
_BLOCK_SPLIT = re.compile(r"\n\s*\n")
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def vtt_to_text(raw: str) -> str:
    """WebVTT -> one 'Speaker: text' line per run of speech. Consecutive cues
    from the same speaker are merged; timings, cue ids and headers are dropped."""
    raw = raw.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    turns = []  # [speaker or None, text]

    for block in _BLOCK_SPLIT.split(raw):
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        timing_idx = next((i for i, ln in enumerate(lines) if _TIMING.match(ln)), None)
        if timing_idx is None:
            continue  # WEBVTT header, NOTE, STYLE and REGION blocks have no timing line
        payload = " ".join(lines[timing_idx + 1:])
        if not payload:
            continue

        speaker = None
        voice = _VOICE_OPEN.search(payload)
        if voice:
            speaker = html.unescape(voice.group(1)).strip()
        text = html.unescape(_ANY_TAG.sub("", payload)).strip()
        if not text:
            continue

        if turns and speaker is not None and turns[-1][0] == speaker:
            turns[-1][1] += " " + text
        else:
            turns.append([speaker, text])

    return "\n".join(f"{s}: {t}" if s else t for s, t in turns)


def docx_to_text(path) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = ElementTree.fromstring(xml)

    paragraphs = []
    for p in root.iter(f"{_W}p"):
        parts = []
        for node in p.iter():
            if node.tag == f"{_W}t":
                parts.append(node.text or "")
            elif node.tag == f"{_W}tab":
                parts.append("\t")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        line = "".join(parts).strip()
        if line:
            paragraphs.append(line)
    return "\n".join(paragraphs)


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):  # Windows Notepad "Unicode"
            return raw.decode("utf-16")
        return raw.decode("latin-1")


def read_transcript_file(path) -> str:
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".docx":
        return docx_to_text(path)
    text = _decode(path.read_bytes())
    if ext == ".vtt" or text.lstrip().upper().startswith("WEBVTT"):
        return vtt_to_text(text)
    return text