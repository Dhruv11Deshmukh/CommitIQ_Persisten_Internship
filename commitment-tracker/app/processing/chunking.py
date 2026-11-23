"""
Splits long text (a one-hour meeting transcript can be 50,000+ characters)
into pieces small enough for the model to read carefully, breaking on line
boundaries so a sentence is never cut in half, with a little overlap so a
commitment that straddles a boundary is still seen whole.
"""
from typing import List


def chunk_text(text: str, max_chars: int = 6000, overlap_chars: int = 400) -> List[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    # Break up any single enormous line so nothing can exceed max_chars.
    pieces = []
    for line in text.splitlines(keepends=True):
        while len(line) > max_chars:
            pieces.append(line[:max_chars])
            line = line[max_chars:]
        pieces.append(line)

    chunks, current, size, fresh = [], [], 0, False
    for piece in pieces:
        if current and fresh and size + len(piece) > max_chars:
            chunks.append("".join(current))
            tail, tail_size = [], 0
            for p in reversed(current):  # carry the last few lines into the next chunk
                if tail_size + len(p) > overlap_chars:
                    break
                tail.insert(0, p)
                tail_size += len(p)
            current, size, fresh = tail, tail_size, False
        current.append(piece)
        size += len(piece)
        fresh = True

    if current and fresh:
        chunks.append("".join(current))
    return [c.strip() for c in chunks if c.strip()]