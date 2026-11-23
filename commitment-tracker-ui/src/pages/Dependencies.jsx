import { useEffect, useMemo, useState, useCallback } from "react";

const API = import.meta.env.VITE_API_URL || "http://localhost:8000";
const W = 210, H = 62, GX = 90, GY = 28;
const jget = (p) => fetch(`${API}${p}`).then((r) => r.json());
const jpost = (p, body) =>
  fetch(`${API}${p}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

function layout(nodes, edges) {
  const ids = nodes.map((n) => n.id);
  const up = Object.fromEntries(ids.map((i) => [i, []]));
  edges.forEach((e) => up[e.to] && up[e.to].push(e.from));
  const layer = Object.fromEntries(ids.map((i) => [i, 0]));
  for (let k = 0; k < ids.length; k++)
    ids.forEach((i) => up[i].forEach((u) => { if (layer[i] < layer[u] + 1 && layer[u] + 1 <= ids.length) layer[i] = layer[u] + 1; }));
  const cols = {};
  ids.forEach((i) => (cols[layer[i]] ||= []).push(i));
  const pos = {};
  Object.entries(cols).forEach(([c, list]) => list.forEach((id, r) => (pos[id] = { x: 20 + c * (W + GX), y: 20 + r * (H + GY) })));
  const maxC = Math.max(0, ...Object.keys(cols).map(Number));
  const maxR = Math.max(1, ...Object.values(cols).map((l) => l.length));
  return { pos, width: 40 + (maxC + 1) * (W + GX), height: 40 + maxR * (H + GY) };
}

const fmt = (d) => (d ? new Date(d).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "no date");
const box = { background: "#f5f5f4", border: "1px solid #e7e5e4", borderRadius: 8, padding: 10, marginBottom: 8 };
const btn = { padding: "5px 12px", borderRadius: 8, border: "1px solid #d6d3d1", background: "transparent", color: "inherit", fontSize: 13, cursor: "pointer" };
const btnDark = { padding: "5px 12px", borderRadius: 8, background: "#1c1917", color: "#fff", border: "1px solid #1c1917", fontSize: 13, cursor: "pointer" };
const tag = { fontSize: 11, border: "1px solid #d6d3d1", borderRadius: 999, padding: "1px 8px", color: "#57534e" };

export default function Dependencies() {
  const [g, setG] = useState({ nodes: [], edges: [], cycles: [], critical_path: [], delays: [] });
  const [keys, setKeys] = useState([]);          // [{id,key,owner,label,status}]
  const [links, setLinks] = useState([]);
  const [sugs, setSugs] = useState([]);
  const [a, setA] = useState(""), [b, setB] = useState("");
  const [msg, setMsg] = useState(""), [choices, setChoices] = useState(null);
  const [sel, setSel] = useState(null), [impact, setImpact] = useState([]);
  const [who, setWho] = useState(""), [whoRes, setWhoRes] = useState(null);
  const [scanning, setScanning] = useState(false);

  const load = useCallback(async () => {
    const [gr, ks, ls, ss] = await Promise.all([
      jget("/dependencies/graph"), jget("/dependencies/keys?all=true"),
      jget("/dependencies/links"), jget("/dependencies/suggestions"),
    ]).catch(() => []);
    if (gr) setG(gr); if (ks) setKeys(ks); if (ls) setLinks(ls); if (ss) setSugs(ss);
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [load]);

  const { pos, width, height } = useMemo(() => layout(g.nodes, g.edges), [g]);
  const keyOf = useMemo(() => Object.fromEntries(keys.map((k) => [k.id, k.key])), [keys]);
  const byId = useMemo(() => Object.fromEntries(g.nodes.map((n) => [n.id, n])), [g]);
  const nm = (id) => keyOf[id] || `#${id}`;

  async function add(taskRef = a, upRef = b) {
    setMsg(""); setChoices(null);
    const r = await jpost("/dependencies/link", { task: taskRef, depends_on: upRef });
    if (r.ok) { setA(""); setB(""); setMsg(`Linked: ${taskRef} now waits on ${upRef}.`); load(); return; }
    const d = (await r.json()).detail;
    if (d && d.reason === "ambiguous") { setChoices({ ...d, taskRef, upRef }); setMsg(`“${d.ref}” matches more than one. Pick one:`); }
    else if (d && d.reason === "not_found") setMsg(`I couldn't find a commitment matching “${d.ref}”.`);
    else setMsg(typeof d === "string" ? d : "Failed");
  }
  function pickChoice(c) {
    const t = choices.which === "task" ? c.key : choices.taskRef, u = choices.which === "depends_on" ? c.key : choices.upRef;
    add(t, u);
  }
  async function remove(id) { await fetch(`${API}/dependencies/${id}`, { method: "DELETE" }); load(); }
  async function pick(id) { setSel(id); setImpact(await jget(`/dependencies/impact/${id}`).catch(() => [])); }
  async function act(s, what) {
    const r = await jpost(`/dependencies/suggestions/${s.id}/${what}`);
    if (!r.ok) { const d = (await r.json()).detail; setMsg(typeof d === "string" ? d : d?.message || "Couldn't do that"); }
    load();
  }
  async function scan() {
    setScanning(true);
    const r = await jpost("/dependencies/auto/scan", { limit: 10 }).then((x) => x.json()).catch(() => null);
    setScanning(false);
    setMsg(r ? `Checked ${r.scanned ?? 0} message(s): ${r.linked ?? 0} linked, ${r.suggested ?? 0} suggested.` : "Scan failed");
    load();
  }
  async function ask() {
    if (!who.trim()) return;
    setWhoRes(await jget(`/dependencies/who?person=${encodeURIComponent(who)}&mine=true`).catch(() => null));
  }

  const border = (n) => (n.critical ? "#1c1917" : "#d6d3d1");

  return (
    <div style={{ padding: 24 }}>
      <h1 style={{ fontSize: 22, fontWeight: 600, marginBottom: 4 }}>Dependencies</h1>
      <p style={{ color: "#78716c", marginBottom: 16 }}>
        Found automatically in your messages, or linked by name. Arrows point from a commitment to the ones that wait for it.
        Bold line = critical path · ⚠ = at risk from an upstream delay · dashed box = circular.
      </p>

      {sugs.length > 0 && (
        <div style={{ ...box, background: "#fff" }}>
          <b>Detected in your messages ({sugs.length})</b>
          {sugs.map((s) => (
            <div key={s.id} style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid #e7e5e4" }}>
              <div><b>{s.task?.key}</b> may wait on <b>{s.upstream ? s.upstream.key : `${s.owner || "someone"}: ${s.text || "a deliverable"}`}</b></div>
              {s.evidence && <div style={{ color: "#78716c", fontSize: 13, margin: "2px 0 6px" }}>“{s.evidence}”</div>}
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {s.upstream && <button style={btnDark} onClick={() => act(s, "link")}>Link them</button>}
                {!s.upstream && s.text && <button style={btnDark} onClick={() => act(s, "track")}>Track {s.owner ? `${s.owner}'s` : "this"} task &amp; link</button>}
                <button style={btn} onClick={() => act(s, "dismiss")}>Dismiss</button>
              </div>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
        <datalist id="dep-keys">{keys.map((k) => <option key={k.id} value={k.key}>{k.owner} · {k.label}</option>)}</datalist>
        <input list="dep-keys" value={a} onChange={(e) => setA(e.target.value)} placeholder="e.g. final-presentation" style={{ padding: 6, minWidth: 200 }} />
        <span style={{ alignSelf: "center" }}>depends on</span>
        <input list="dep-keys" value={b} onChange={(e) => setB(e.target.value)} placeholder="e.g. system-architecture" style={{ padding: 6, minWidth: 200 }} />
        <button onClick={() => add()} disabled={!a || !b} style={btnDark}>Add link</button>
        <button onClick={scan} disabled={scanning} style={btn}>{scanning ? "Reading messages…" : "Scan messages now"}</button>
      </div>
      <p style={{ color: "#78716c", fontSize: 12, marginBottom: 8 }}>Type a key, or just a few words (“james architecture”). Keys are shown on every box below.</p>
      {msg && <div style={{ ...box, background: "#fff" }}>{msg}
        {choices && <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
          {choices.candidates.map((c) => <button key={c.id} style={btn} onClick={() => pickChoice(c)}>{c.key} · {c.owner}</button>)}
        </div>}
      </div>}

      <div style={{ ...box, background: "#fff" }}>
        <b>Do my commitments depend on…</b>
        <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
          <input value={who} onChange={(e) => setWho(e.target.value)} onKeyDown={(e) => e.key === "Enter" && ask()} placeholder="a person, e.g. James" style={{ padding: 6, minWidth: 200 }} />
          <button style={btnDark} onClick={ask}>Check</button>
        </div>
        {whoRes && (
          <div style={{ marginTop: 10 }}>
            <div><b>{whoRes.summary}</b></div>
            {whoRes.waiting.map((w, i) => (
              <div key={i} style={{ fontSize: 14, marginTop: 4 }}>• {w.task.key} ({w.task.owner}) waits on {w.via.key}{w.direct ? "" : " (indirectly)"}</div>
            ))}
            {whoRes.untracked.map((u) => (
              <div key={u.id} style={{ fontSize: 14, marginTop: 4 }}>• {u.task.key} may wait on {u.owner}: {u.text} <span style={tag}>not tracked yet</span></div>
            ))}
          </div>
        )}
      </div>

      {g.cycles.length > 0 && <div style={box}>Circular dependency: {g.cycles.map((c) => c.map(nm).join(" → ")).join(" | ")}</div>}
      {g.delays.map((d) => (
        <div key={d.upstream} style={box}>
          ⚠ <b>{byId[d.upstream]?.owner}</b>'s “{byId[d.upstream]?.label}” is {d.reason} → {d.affected.length} downstream affected ({[...new Set(d.affected.map((i) => byId[i]?.owner))].join(", ")})
        </div>
      ))}

      <div style={{ overflow: "auto", border: "1px solid #e7e5e4", borderRadius: 12, background: "#fff" }}>
        {g.nodes.length === 0 ? (
          <p style={{ padding: 24, color: "#78716c" }}>No links yet. They appear here when a message says one task waits on another, or when you add one above.</p>
        ) : (
          <svg width={width} height={height}>
            <defs>
              <marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#a8a29e" /></marker>
              <marker id="arc" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#1c1917" /></marker>
            </defs>
            {g.edges.map((e, i) => {
              const p = pos[e.from], q = pos[e.to]; if (!p || !q) return null;
              const x1 = p.x + W, y1 = p.y + H / 2, x2 = q.x, y2 = q.y + H / 2, mx = (x1 + x2) / 2;
              return (
                <g key={i}>
                  <path d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`} fill="none" stroke={e.critical ? "#1c1917" : "#a8a29e"}
                    strokeWidth={e.critical ? 2.5 : 1.25} markerEnd={`url(#${e.critical ? "arc" : "ar"})`} />
                </g>
              );
            })}
            {g.nodes.map((n) => {
              const p = pos[n.id]; if (!p) return null;
              return (
                <g key={n.id} transform={`translate(${p.x},${p.y})`} onClick={() => pick(n.id)} style={{ cursor: "pointer" }}>
                  <rect width={W} height={H} rx="10" fill={n.status === "done" ? "#f5f5f4" : "#fff"} stroke={border(n)}
                    strokeWidth={sel === n.id ? 3 : 1.75} strokeDasharray={n.in_cycle ? "5 3" : undefined} />
                  <text x="10" y="20" fontSize="12" fontWeight="600" fill="#1c1917">{n.delayed_by_upstream ? "⚠ " : ""}{nm(n.id).slice(0, 28)}</text>
                  <text x="10" y="38" fontSize="11" fill="#78716c">{n.owner} · due {fmt(n.due)}</text>
                  <text x="10" y="54" fontSize="10" fill="#a8a29e">{n.status}{n.downstream_count ? ` · blocks ${n.downstream_count}` : ""}</text>
                </g>
              );
            })}
          </svg>
        )}
      </div>

      {links.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <b>All links ({links.length})</b>
          {links.map((l) => (
            <div key={l.id} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", padding: "6px 0", borderTop: "1px solid #e7e5e4" }}>
              <span><b>{l.task.key}</b> waits on <b>{l.depends_on.key}</b> <span style={{ color: "#78716c" }}>({l.depends_on.owner})</span></span>
              <span style={tag}>{l.origin === "auto" ? "auto-detected" : "manual"}</span>
              {l.evidence && <span style={{ color: "#78716c", fontSize: 12 }}>“{l.evidence.slice(0, 80)}”</span>}
              <button style={{ ...btn, marginLeft: "auto" }} onClick={() => confirm("Remove this link?") && remove(l.id)}>Remove</button>
            </div>
          ))}
        </div>
      )}

      {sel && (
        <div style={{ marginTop: 14 }}>
          <b>If {nm(sel)} slips, these are affected ({impact.length}):</b>
          {impact.length === 0 ? <p style={{ color: "#78716c" }}>Nothing depends on it.</p> : (
            <ul style={{ marginTop: 6 }}>{impact.map((i) => <li key={i.id}>{nm(i.id)} — {i.owner}, due {fmt(i.due)} ({i.status})</li>)}</ul>
          )}
        </div>
      )}
    </div>
  );
}