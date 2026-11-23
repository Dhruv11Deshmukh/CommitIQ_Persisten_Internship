import { useEffect, useState, useCallback } from "react";

const API = "http://127.0.0.1:8000";
const fmt = (d) =>
  d ? new Date(d).toLocaleString(undefined, { day: "numeric", month: "short", hour: d.length > 10 && !d.endsWith("T00:00:00") ? "numeric" : undefined, minute: d.length > 10 && !d.endsWith("T00:00:00") ? "2-digit" : undefined }) : "no date";

function Section({ title, items, render }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="mt-5">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-stone-500 mb-2">{title} ({items.length})</h3>
      <ul className="space-y-1.5">{items.map((x, i) => <li key={i} className="text-sm text-stone-800">{render(x)}</li>)}</ul>
    </div>
  );
}

export function BriefCard({ compact = false }) {
  const [kind, setKind] = useState("daily");
  const [b, setB] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() =>
    fetch(`${API}/briefs/latest?kind=${kind}`).then((r) => r.json()).then(setB).catch(() => setB(null)), [kind]);
  useEffect(() => { load(); const t = setInterval(load, 60000); return () => clearInterval(t); }, [load]);

  async function generate() {
    setBusy(true);
    try { setB(await fetch(`${API}/briefs/generate?kind=${kind}`, { method: "POST" }).then((r) => r.json())); }
    finally { setBusy(false); }
  }

  return (
    <div className="rounded-2xl border border-stone-200 bg-white p-6">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex gap-1 rounded-lg bg-stone-100 p-1">
          {["daily", "weekly"].map((k) => (
            <button key={k} onClick={() => setKind(k)}
              className={`px-3 py-1 rounded-md text-sm capitalize ${kind === k ? "bg-white shadow-sm text-stone-900" : "text-stone-500"}`}>{k}</button>
          ))}
        </div>
        <button onClick={generate} disabled={busy}
          className="px-3 py-1.5 rounded-lg bg-stone-900 text-white text-sm disabled:opacity-50">
          {busy ? "Generating…" : "Generate now"}
        </button>
      </div>

      {!b ? (
        <p className="mt-6 text-sm text-stone-500">No {kind} brief yet. It is created automatically each morning, or press Generate now.</p>
      ) : (
        <>
          <h2 className="mt-5 text-xl font-semibold text-stone-900">{b.headline}</h2>
          <p className="mt-1 text-sm text-stone-600">{b.summary}</p>
          <p className="mt-1 text-xs text-stone-400">
            {b.period} · generated {fmt(b.created_at)} · {b.ai_written ? "written by Gemini" : "rule-based summary"}
          </p>

          <Section title="Recommended actions" items={b.actions}
            render={(a) => <span>→ {a.action} <span className="text-stone-400">#{a.commitment_id}</span></span>} />
          {!compact && (
            <>
              <Section title="Overdue" items={b.overdue}
                render={(c) => <span><b>{c.owner}</b> · {c.text} <span className="text-stone-400">was due {fmt(c.due)}</span></span>} />
              <Section title="Critical" items={b.critical}
                render={(c) => <span><b>{c.owner}</b> · {c.text} <span className="text-stone-400">priority {c.priority}</span></span>} />
              <Section title="Upcoming deadlines" items={b.upcoming}
                render={(c) => <span><b>{c.owner}</b> · {c.text} <span className="text-stone-400">due {fmt(c.due)}</span></span>} />
              <Section title="Clashes" items={b.clashes}
                render={(g) => <span>{g.items.map((i) => `${i.text.slice(0, 40)} (${fmt(i.due)})`).join("  ⟷  ")}</span>} />
              <Section title="Delays" items={b.delays}
                render={(d) => <span><b>{d.owner}</b>'s "{d.text}" is {d.reason} → blocks {d.affected.map((a) => `${a.text.slice(0, 30)} (${a.owner})`).join(", ")}</span>} />
              <Section title="What changed" items={b.changes}
                render={(c) => <span className="capitalize">{c.kind}: <span className="normal-case">{c.owner} · {c.text}</span></span>} />
            </>
          )}
        </>
      )}
    </div>
  );
}

export default function Brief() {
  return (
    <div>
      <h1 className="text-2xl font-semibold text-stone-900 mb-1">AI Brief</h1>
      <p className="text-stone-500 mb-6 text-sm">Your commitments, summarised. Generated every morning and every Monday for the week.</p>
      <BriefCard />
    </div>
  );
}