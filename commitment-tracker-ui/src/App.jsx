import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import "./theme/cinematic.css";
import { AuthProvider, useAuth, useRoute } from "./auth/AuthContext";
import Landing from "./pages/Landing";
import { SignIn, SignUp } from "./pages/AuthPages";
import Sidebar from "./components/Sidebar";
import Toast from "./components/CinToast";
import Overview from "./pages/Overview";
import Commitments from "./pages/Commitments";
import Dependencies from "./pages/Dependencies";
import Brief from "./pages/Brief";
import { gsap, reducedMotion } from "./lib/motion";

const API_BASE = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
const WS_URL = API_BASE.replace(/^http/, "ws") + "/ws";

/* ----------------------------------------------------------------- dashboard */
function Dashboard() {
  const { user, signOut } = useAuth();
  const [page, setPage] = useState("commitments");
  const [commitments, setCommitments] = useState([]);
  const [connected, setConnected] = useState(false);
  const [toasts, setToasts] = useState([]);
  const [refreshSignal, setRefreshSignal] = useState(0);
  const toastId = useRef(0);
  const main = useRef(null);

  const fetchCommitments = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/commitments`);
      setCommitments(await res.json());
    } catch (err) {
      console.error("Failed to fetch commitments:", err);
    }
  }, []);

  const dismissToast = useCallback((id) => setToasts((prev) => prev.filter((t) => t.id !== id)), []);
  const pushToast = useCallback((toast, duration = 5000) => {
    toastId.current += 1;
    const id = toastId.current;
    setToasts((prev) => [...prev, { id, ...toast }]);
    setTimeout(() => dismissToast(id), duration);
  }, [dismissToast]);

  useEffect(() => { fetchCommitments(); }, [fetchCommitments]);

  // Tell the backend when this window is hidden/minimised so the chat assistant can open.
  useEffect(() => {
    const send = () =>
      fetch(`${API_BASE}/assistant/main-visibility`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hidden: document.hidden }),
      }).catch(() => {});
    document.addEventListener("visibilitychange", send);
    return () => document.removeEventListener("visibilitychange", send);
  }, []);

  // Live updates; reconnects on its own after a backend restart.
  useEffect(() => {
    let closed = false, ws, retryTimer;
    const connect = () => {
      ws = new WebSocket(WS_URL);
      ws.onopen = () => { setConnected(true); fetchCommitments(); setRefreshSignal((n) => n + 1); };
      ws.onclose = () => { setConnected(false); if (!closed) retryTimer = setTimeout(connect, 2000); };
      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === "commitment_created") pushToast({ message: `${data.owner} → ${data.description}` });
        if (data.type === "nudge_drafted")
          pushToast({ message: "A follow-up draft is ready in Gmail — review before sending", isNudge: true }, 7000);
        fetchCommitments();
        setRefreshSignal((n) => n + 1);
      };
    };
    connect();
    return () => { closed = true; clearTimeout(retryTimer); if (ws) ws.close(); };
  }, [fetchCommitments, pushToast]);

  // Page change: content glides in.
  useLayoutEffect(() => {
    if (!main.current || reducedMotion()) return;
    const ctx = gsap.context(() => {
      gsap.from(main.current.children, { y: 22, opacity: 0, duration: 0.7, stagger: 0.07, ease: "power3.out", clearProps: "transform,opacity" });
    }, main);
    return () => ctx.revert();
  }, [page]);

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "var(--bg)", position: "relative" }}>
      <style>{`@keyframes slideIn { from { opacity: 0; transform: translateX(20px); } to { opacity: 1; transform: translateX(0); } }`}</style>
      <div aria-hidden="true" style={{ position: "fixed", inset: 0, pointerEvents: "none", background:
        "radial-gradient(45% 40% at 85% 0%, rgba(229,9,20,.07), transparent 70%), radial-gradient(40% 40% at 0% 100%, rgba(255,255,255,.035), transparent 70%)" }} />

      <div style={{ position: "fixed", top: 20, right: 20, zIndex: 50, display: "flex", flexDirection: "column", gap: 10 }}>
        {toasts.map((t) => <Toast key={t.id} message={t.message} isNudge={t.isNudge} onDismiss={() => dismissToast(t.id)} />)}
      </div>

      <Sidebar active={page} onNavigate={setPage} connected={connected} user={user} onSignOut={signOut} />

      <main ref={main} style={{ flex: 1, minWidth: 0, maxWidth: 1180, padding: "2.5rem 3rem", position: "relative" }}>
        {page === "overview" && <Overview commitments={commitments} refreshSignal={refreshSignal} onNavigate={setPage} />}
        {page === "commitments" && <Commitments commitments={commitments} onSaved={fetchCommitments} />}
        {page === "dependencies" && <Dependencies />}
        {page === "brief" && <Brief />}
      </main>
    </div>
  );
}

/* ---------------------------------------------------------------------- gate */
function Gate() {
  const { status, refresh } = useAuth();
  const [route, go] = useRoute();

  useEffect(() => { document.documentElement.classList.add("cinematic"); }, []);

  if (status === "loading") return <div className="splash">Loading…</div>;
  if (status === "offline")
    return (
      <div className="splash">
        <div>
          <p>Can't reach the CommitIQ server. Is the backend running?</p>
          <button className="cin-btn primary small" onClick={refresh}>Try again</button>
        </div>
      </div>
    );
  if (status === "authed") return <Dashboard />;
  if (route === "signin") return <SignIn go={go} />;
  if (route === "signup") return <SignUp go={go} />;
  return <Landing go={go} />;
}

export default function App() {
  return (
    <AuthProvider>
      <Gate />
    </AuthProvider>
  );
}
