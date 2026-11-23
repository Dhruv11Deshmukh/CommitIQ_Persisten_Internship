import { Suspense, lazy, useEffect, useLayoutEffect, useRef, useState } from "react";
import "../theme/landing.css";
const Hero3D = lazy(() => import("../components/Hero3D"));   // three.js loads only on public pages
import { LogoMark } from "../components/Logo";
import GoogleButton from "../components/GoogleButton";
import { readHashError, useAuth } from "../auth/AuthContext";
import { gsap, reducedMotion, useTilt } from "../lib/motion";

const TAGLINE = "AI commitment intelligence";
const USERNAME_RULE = /^[a-z][a-z0-9_]{2,19}$/;

function Shell({ title, go, children }) {
  const card = useRef(null);
  useTilt(card, { max: 3, lift: 0 });
  useLayoutEffect(() => {
    if (reducedMotion()) return;
    const ctx = gsap.context(() => {
      gsap.from(card.current, { y: 40, opacity: 0, scale: 0.97, duration: 1, ease: "power3.out" });
      gsap.from(".au-card .au-field, .au-card .cin-btn, .au-card .au-or", { y: 14, opacity: 0, duration: 0.7, stagger: 0.06, delay: 0.25, ease: "power3.out", clearProps: "all" });
    });
    return () => ctx.revert();
  }, []);
  return (
    <div className="au cin-grain">
      <div className="au-bg" />
      <Suspense fallback={null}><Hero3D variant="ambient" /></Suspense>
      <div className="cin-vignette" style={{ position: "absolute", inset: 0, pointerEvents: "none", zIndex: 1 }} />
      <button className="cin-btn ghost small au-back" onClick={() => go("")}>← Back</button>
      <div className="au-card" ref={card}>
        <div className="au-brand">
          <LogoMark size={44} />
          <div><b>CommitIQ</b><small>{TAGLINE}</small></div>
        </div>
        <h1>{title}</h1>
        {children}
      </div>
    </div>
  );
}

function Field({ label, children }) {
  return <div className="au-field"><label className="cin-label">{label}</label>{children}</div>;
}

function Password({ value, onChange, autoComplete }) {
  const [show, setShow] = useState(false);
  return (
    <div className="au-pw">
      <input className="cin-input" type={show ? "text" : "password"} value={value} onChange={onChange}
        autoComplete={autoComplete} required style={{ paddingRight: "4.6rem" }} />
      <button type="button" onClick={() => setShow((s) => !s)}>{show ? "Hide" : "Show"}</button>
    </div>
  );
}

const GOOGLE_NOTE = "Signing in with Google also connects Gmail so CommitIQ can find your commitments. It only reads mail and saves drafts; it never sends.";

export function SignIn({ go }) {
  const { signIn, googleUrl } = useAuth();
  const [id, setId] = useState(""), [pw, setPw] = useState("");
  const [err, setErr] = useState(readHashError()), [busy, setBusy] = useState(false);

  useEffect(() => { if (err) history.replaceState(null, "", "#/signin"); }, []);       // eslint-disable-line

  async function submit(e) {
    e.preventDefault(); setErr(""); setBusy(true);
    try { await signIn(id, pw); } catch (x) { setErr(x.message); setBusy(false); }
  }

  return (
    <Shell title="Sign in" go={go}>
      {err && <div className="au-error" role="alert">{err}</div>}
      <div className="au-field"><GoogleButton href={googleUrl} /></div>
      <div className="au-or">or</div>
      <form onSubmit={submit}>
        <Field label="Email or username">
          <input className="cin-input" value={id} onChange={(e) => setId(e.target.value)} autoComplete="username" required autoFocus />
        </Field>
        <Field label="Password"><Password value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="current-password" /></Field>
        <button className="cin-btn primary block" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
      <p className="au-note">{GOOGLE_NOTE}</p>
      <p className="au-switch">No account? <button onClick={() => go("signup")}>Create one</button></p>
    </Shell>
  );
}

export function SignUp({ go }) {
  const { signUp, googleUrl } = useAuth();
  const [f, setF] = useState({ display_name: "", username: "", email: "", password: "" });
  const [err, setErr] = useState(""), [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF({ ...f, [k]: k === "username" ? e.target.value.toLowerCase() : e.target.value });

  async function submit(e) {
    e.preventDefault(); setErr("");
    if (!USERNAME_RULE.test(f.username)) return setErr("Username: 3–20 characters, start with a letter, lowercase letters, numbers, underscore.");
    if (f.password.length < 8) return setErr("Password must be at least 8 characters.");
    setBusy(true);
    try { await signUp(f); } catch (x) { setErr(x.message); setBusy(false); }
  }

  return (
    <Shell title="Create your account" go={go}>
      {err && <div className="au-error" role="alert">{err}</div>}
      <div className="au-field"><GoogleButton href={googleUrl} label="Sign up with Google" /></div>
      <div className="au-or">or</div>
      <form onSubmit={submit}>
        <Field label="Display name"><input className="cin-input" value={f.display_name} onChange={set("display_name")} autoComplete="name" required /></Field>
        <Field label="Username"><input className="cin-input" value={f.username} onChange={set("username")} autoComplete="username" required /></Field>
        <Field label="Email"><input className="cin-input" type="email" value={f.email} onChange={set("email")} autoComplete="email" required /></Field>
        <Field label="Password"><Password value={f.password} onChange={set("password")} autoComplete="new-password" /></Field>
        <p className="au-help">Username: 3–20 characters, start with a letter, lowercase letters, numbers, underscore. Password: at least 8 characters.</p>
        <button className="cin-btn primary block" disabled={busy}>{busy ? "Creating…" : "Create account"}</button>
      </form>
      <p className="au-note">{GOOGLE_NOTE}</p>
      <p className="au-switch">Already registered? <button onClick={() => go("signin")}>Sign in</button></p>
    </Shell>
  );
}
