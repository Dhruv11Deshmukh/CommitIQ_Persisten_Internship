"""Descriptive keys for commitments and a forgiving resolver ('final presentation', '#12', 'james architecture')."""
import re
from sqlalchemy.exc import IntegrityError
from app.models import Commitment
from app.dependencies.models_smart import CommitmentKey

STOP = set("""a an the to of for from with without me you your my our their his her its us we i he she they it this that these
those and or but if then than so as at by in on into onto up out over under about after before until once when while
please will shall can could would should may might must need needs want wants have has had do does did be is are was were
been being get gets got give gives send sends sent submit submits share shares provide provides prepare prepares finish
finishes complete completes review reviews deliver delivers update updates make makes create creates write writes
let know check follow going just also all any some each every new""".split())


def slugify(text, max_len=48):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:max_len].strip("-")


def tokens(text):
    out = []
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if w in STOP or len(w) < 2:
            continue
        out.append(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w)
    return out


def _first_name(owner):
    return slugify((owner or "").split()[0]) if owner else ""


def make_key(c, taken):
    head = re.split(r"\b(?:to|for|from|by|with|at|on|in|before|after|until|once)\b", c.description or "", maxsplit=1)[0]
    words = (tokens(head) or tokens(c.description))[:3]     # "Send the system architecture to Dhruv" -> system-architecture
    base = slugify("-".join(words)) or slugify(f"{_first_name(c.owner)}-task") or "task"
    cand = base
    if cand in taken and _first_name(c.owner):
        cand = f"{base}-{_first_name(c.owner)}"
    n = 2
    while cand in taken:
        cand = f"{base}-{n}"
        n += 1
    return cand


def ensure_keys(db):
    """Give every commitment a key (once). Returns {commitment_id: key}."""
    for _ in range(2):
        rows = db.query(CommitmentKey).all()
        have = {r.commitment_id: r.key for r in rows}
        taken = set(have.values())
        missing = [c for c in db.query(Commitment).order_by(Commitment.id).all() if c.id not in have]
        if not missing:
            return have
        for c in missing:
            k = make_key(c, taken)
            taken.add(k)
            have[c.id] = k
            db.add(CommitmentKey(commitment_id=c.id, key=k))
        try:
            db.commit()
            return have
        except IntegrityError:
            db.rollback()
    return {r.commitment_id: r.key for r in db.query(CommitmentKey).all()}


def _status(c):
    return getattr(c.status, "value", c.status)


def card(c, key):
    return {"id": c.id, "key": key, "owner": c.owner, "label": (c.description or "")[:80],
            "due": c.due_date.isoformat() if c.due_date else None, "status": _status(c)}


def resolve(db, ref, prefer_owner=None):
    """-> (commitment_id or None, candidates). Accepts '#12', '12', an exact/partial key, or loose words."""
    ref = (ref or "").strip().strip("\"'")
    keys = ensure_keys(db)
    cs = {c.id: c for c in db.query(Commitment).all()}
    m = re.fullmatch(r"#?(\d+)", ref)
    if m:
        i = int(m.group(1))
        return (i, []) if i in cs else (None, [])
    slug = slugify(ref)
    by_key = {k: i for i, k in keys.items()}
    if slug in by_key:
        return by_key[slug], []
    pref = [i for k, i in by_key.items() if k.startswith(slug)] if slug else []
    if len(pref) == 1:
        return pref[0], []
    want = set(tokens(ref))
    if not want:
        return None, []
    scored = []
    for i, c in cs.items():
        have = set(tokens(keys.get(i, "").replace("-", " "))) | set(tokens(c.description)) | set(tokens(c.owner))
        s = len(want & have) / len(want)
        if prefer_owner and set(tokens(prefer_owner)) & set(tokens(c.owner)):
            s += 0.15
        if _status(c) in ("done", "cancelled"):
            s -= 0.1
        if s > 0:
            scored.append((s, i))
    scored.sort(reverse=True)
    if scored and scored[0][0] >= 0.66 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.2):
        return scored[0][1], []
    return None, [card(cs[i], keys.get(i)) for s, i in scored[:5] if s >= 0.34]
