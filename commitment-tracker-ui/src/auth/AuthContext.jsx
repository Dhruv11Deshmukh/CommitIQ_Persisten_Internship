import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

export const API = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
const KEY = "commitiq_token";

const store = {
  get: () => { try { return localStorage.getItem(KEY); } catch { return null; } },
  set: (v) => { try { v ? localStorage.setItem(KEY, v) : localStorage.removeItem(KEY); } catch { /* private mode */ } },
};

const Ctx = createContext(null);
export const useAuth = () => useContext(Ctx);

/** Tiny hash router: "" (landing) | "signin" | "signup". Avoids adding a routing dependency. */
export function useRoute() {
  const read = () => (window.location.hash.replace(/^#\/?/, "").split("?")[0] || "");
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const on = () => setRoute(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const go = useCallback((to) => { window.location.hash = to ? `/${to}` : "/"; window.scrollTo(0, 0); }, []);
  return [route, go];
}

/** Message left in the URL by a failed Google sign-in (#/signin?error=...). */
export function readHashError() {
  const q = window.location.hash.split("?")[1];
  return q ? new URLSearchParams(q).get("error") || "" : "";
}

async function call(path, { method = "GET", body, token } = {}) {
  let res;
  try {
    res = await fetch(`${API}${path}`, {
      method,
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    const e = new Error("Can't reach the server. Is the backend running?"); e.network = true; throw e;
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    const e = new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x.msg).join(". ") : "Something went wrong.");
    e.status = res.status; throw e;
  }
  return data;
}

export function AuthProvider({ children }) {
  const [status, setStatus] = useState("loading");        // loading | anon | authed | offline
  const [user, setUser] = useState(null);

  const refresh = useCallback(async () => {
    // Returning from Google: the backend put the session token in the URL fragment.
    const m = window.location.hash.match(/^#\/auth\?token=([^&]+)/);
    if (m) { store.set(decodeURIComponent(m[1])); history.replaceState(null, "", window.location.pathname + "#/"); }

    const token = store.get();
    if (!token) { setUser(null); setStatus("anon"); return; }
    try {
      setUser(await call("/auth/me", { token })); setStatus("authed");
    } catch (e) {
      if (e.network) { setStatus("offline"); return; }
      store.set(null); setUser(null); setStatus("anon");
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const accept = ({ token, user: u }) => { store.set(token); setUser(u); setStatus("authed"); window.location.hash = "/"; };

  const value = useMemo(() => ({
    status, user, refresh,
    signIn: async (identifier, password) => accept(await call("/auth/login", { method: "POST", body: { identifier, password } })),
    signUp: async (form) => accept(await call("/auth/signup", { method: "POST", body: form })),
    signOut: () => { store.set(null); setUser(null); setStatus("anon"); window.location.hash = "/"; },
    googleUrl: `${API}/auth/google/start`,
  }), [status, user, refresh]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
