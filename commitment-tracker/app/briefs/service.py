"""Daily / weekly brief. Facts are computed by code (always correct);
Gemini only writes the headline, summary and recommended actions - validated against the facts."""
import os, json, re, asyncio
from datetime import datetime, timedelta, date
from app.models import Commitment
from app.briefs.models import Brief

BRIEF_HOUR = int(os.getenv("BRIEF_HOUR", "8"))        # local hour for automatic generation
ACTIVE = ("open", "at_risk", "overdue")
CRITICAL_SCORE = int(os.getenv("BRIEF_CRITICAL_SCORE", "70"))


def _st(c):
    return getattr(c.status, "value", c.status)


def _deadline(due):
    if due is None:
        return None
    if due.hour == 0 and due.minute == 0 and due.second == 0:
        return due + timedelta(hours=23, minutes=59)
    return due


def _item(c, text=None):
    return {"id": c.id, "owner": c.owner, "text": text or c.description[:140],
            "due": c.due_date.isoformat() if c.due_date else None,
            "priority": int(c.priority_score or 0)}


def period_key(kind, now):
    if kind == "weekly":
        y, w, _ = now.isocalendar()
        return f"{y}-W{w:02d}"
    return now.date().isoformat()


def gather_facts(commitments, kind="daily", now=None, delays=None):
    now = now or datetime.now()
    horizon = timedelta(days=7 if kind == "weekly" else 3)
    window = timedelta(days=7 if kind == "weekly" else 1)
    by_id = {c.id: c for c in commitments}
    active = [c for c in commitments if _st(c) in ACTIVE]

    critical = sorted([c for c in active if (c.priority_score or 0) >= CRITICAL_SCORE],
                      key=lambda c: -(c.priority_score or 0))[:8]
    overdue = sorted([c for c in active if _deadline(c.due_date) and _deadline(c.due_date) < now],
                     key=lambda c: c.due_date)[:10]
    upcoming = sorted([c for c in active if _deadline(c.due_date) and now <= _deadline(c.due_date) <= now + horizon],
                      key=lambda c: c.due_date)[:12]

    groups = {}
    for c in active:
        g = getattr(c, "clash_group_id", None)
        if g:
            groups.setdefault(g, []).append(c)
    clashes = [{"group": g, "items": [_item(c) for c in cs]} for g, cs in groups.items() if len(cs) > 1][:6]

    changes = []
    for c in commitments:
        if c.created_at and now - c.created_at <= window:
            changes.append({"kind": "new", **_item(c)})
        done_at = getattr(c, "completed_at", None)
        if done_at and now - done_at <= window + timedelta(hours=6):   # completed_at is naive UTC
            changes.append({"kind": "completed", **_item(c)})
    changes = changes[:15]

    delay_items = []
    for d in (delays or []):
        up = by_id.get(d["upstream"])
        if not up:
            continue
        aff = [by_id[a] for a in d["affected"] if a in by_id and _st(by_id[a]) in ACTIVE]
        if aff:
            delay_items.append({"id": up.id, "owner": up.owner, "reason": d["reason"],
                                "text": up.description[:100],
                                "affected": [{"id": a.id, "owner": a.owner, "text": a.description[:60]} for a in aff[:5]]})
    return {
        "kind": kind, "generated_at": now.isoformat(timespec="minutes"),
        "counts": {"open": len(active), "overdue": len(overdue), "critical": len(critical), "upcoming": len(upcoming)},
        "critical": [_item(c) for c in critical], "overdue": [_item(c) for c in overdue],
        "upcoming": [_item(c) for c in upcoming], "clashes": clashes,
        "delays": delay_items, "changes": changes,
    }


def fallback_narrative(f):
    n = f["counts"]
    parts = []
    if n["overdue"]: parts.append(f"{n['overdue']} overdue")
    if n["critical"]: parts.append(f"{n['critical']} critical")
    if n["upcoming"]: parts.append(f"{n['upcoming']} due soon")
    headline = ", ".join(parts).capitalize() if parts else "All clear"
    actions = []
    for c in f["overdue"][:3]:
        actions.append({"commitment_id": c["id"], "action": f"Complete or reschedule: {c['text'][:70]}"})
    for c in f["critical"][:2]:
        if all(a["commitment_id"] != c["id"] for a in actions):
            actions.append({"commitment_id": c["id"], "action": f"Prioritise: {c['text'][:70]}"})
    for d in f["delays"][:1]:
        actions.append({"commitment_id": d["id"], "action": f"Unblock {d['owner']}'s task - {len(d['affected'])} items wait on it"})
    for g in f["clashes"][:1]:
        actions.append({"commitment_id": g["items"][0]["id"], "action": "Resolve the schedule clash"})
    summary = (f"You have {n['open']} open commitments: {n['overdue']} overdue, "
               f"{n['critical']} critical and {n['upcoming']} due in the next "
               f"{'7' if f['kind'] == 'weekly' else '3'} days.")
    return {"headline": headline, "summary": summary, "actions": actions[:5]}, 0


def ai_narrative(f):
    from app.processing.extractor import client, MODEL     # reuse existing Gemini client/key
    ids = {i["id"] for k in ("critical", "overdue", "upcoming") for i in f[k]}
    ids |= {d["id"] for d in f["delays"]} | {i["id"] for g in f["clashes"] for i in g["items"]}
    prompt = (
        f"You are an executive assistant writing a {f['kind']} commitment brief. "
        "Use ONLY the facts below; never invent people, tasks, dates or ids.\n"
        'Return JSON only: {"headline": "<max 10 words>", "summary": "<2-3 sentences>", '
        '"actions": [{"commitment_id": <id from facts>, "action": "<one imperative sentence>"}]} '
        "with at most 5 actions, most urgent first.\n\nFACTS:\n" + json.dumps(f, default=str)[:12000])
    resp = client.models.generate_content(model=MODEL, contents=prompt)
    raw = re.sub(r"^```(?:json)?|```$", "", (resp.text or "").strip(), flags=re.M).strip()
    data = json.loads(raw)
    acts = [{"commitment_id": int(a["commitment_id"]), "action": str(a["action"])[:160]}
            for a in data.get("actions", []) if int(a.get("commitment_id", -1)) in ids][:5]
    if not str(data.get("headline", "")).strip() or not str(data.get("summary", "")).strip():
        raise ValueError("empty narrative")
    return {"headline": str(data["headline"])[:100], "summary": str(data["summary"])[:600], "actions": acts}, 1


def _delays(db):
    try:
        from app.dependencies.router import _load
        from app.dependencies import graph as G
        cs, _, edges = _load(db)
        active = {i for i, c in cs.items() if _st(c) in ACTIVE}
        e = [x for x in edges if x[0] in active and x[1] in active]
        due = {i: G.effective_deadline(cs[i].due_date) for i in active}
        return [{"upstream": u, **r} for u, r in G.delay_report(e, due).items()]
    except Exception:
        return []


def generate_brief(db, kind="daily", force=False, now=None):
    now = now or datetime.now()
    key = period_key(kind, now)
    existing = db.query(Brief).filter_by(kind=kind, period_key=key).first()
    if existing and not force:
        return existing
    facts = gather_facts(db.query(Commitment).all(), kind, now, _delays(db))
    try:
        narrative, ai = ai_narrative(facts)
    except Exception as e:
        print(f"Brief: Gemini unavailable ({str(e)[:80]}), using rule-based text")
        narrative, ai = fallback_narrative(facts)
    content = json.dumps({**facts, **narrative}, default=str)
    if existing:
        existing.content, existing.ai_written, existing.created_at = content, ai, now
        row = existing
    else:
        row = Brief(kind=kind, period_key=key, content=content, ai_written=ai, created_at=now)
        db.add(row)
    db.commit()
    return row


def announce(db, kind="daily"):
    """Create a desktop notification for the brief. daily: every call. weekly: once per ISO week."""
    now = datetime.now()
    key = period_key(kind, now)
    row = generate_brief(db, kind)
    c = json.loads(row.content)
    if kind == "weekly":
        dedupe = f"brief:weekly:{key}"
    else:
        dedupe = f"brief:daily:{now.timestamp()}"
    try:
        from app.notifications.models import Notification
    except Exception:
        return {"announced": False, "reason": "notifications module missing"}
    if db.query(Notification).filter_by(dedupe_key=dedupe).first():
        return {"announced": False, "reason": "already announced"}
    first = c["actions"][0]["action"] if c.get("actions") else ""
    db.add(Notification(kind="brief", dedupe_key=dedupe,
                        title=f"{kind.capitalize()} brief: {c['headline']}",
                        body=f"{c['summary']}\n{('Next: ' + first) if first else ''}".strip()))
    db.commit()
    return {"announced": True}


async def brief_loop():
    from app.database import SessionLocal
    while True:
        try:
            now = datetime.now()
            if now.hour >= BRIEF_HOUR:
                kinds = ["daily"] + (["weekly"] if now.weekday() == 0 else [])
                db = SessionLocal()
                try:
                    for k in kinds:
                        await asyncio.to_thread(generate_brief, db, k)
                finally:
                    db.close()
        except Exception as e:
            print(f"Brief loop error: {e}")
        await asyncio.sleep(900)