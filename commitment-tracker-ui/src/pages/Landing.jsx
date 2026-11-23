import { Suspense, lazy, useEffect, useLayoutEffect, useRef, useState } from "react";
import "../theme/landing.css";
const Hero3D = lazy(() => import("../components/Hero3D"));   // three.js loads only on public pages
import Logo from "../components/Logo";
import GoogleButton from "../components/GoogleButton";
import { useAuth } from "../auth/AuthContext";
import { gsap, reducedMotion, useMagnetic, useReveal, useTilt } from "../lib/motion";

const I = (d) => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{d}</svg>
);
const ICONS = {
  mail: I(<><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M3 7l9 6 9-6" /></>),
  clash: I(<><rect x="3" y="4" width="11" height="9" rx="2" /><rect x="10" y="11" width="11" height="9" rx="2" /></>),
  gauge: I(<><path d="M4 18a8 8 0 1116 0" /><path d="M12 18l4-6" /></>),
  graph: I(<><circle cx="5" cy="6" r="2" /><circle cx="19" cy="6" r="2" /><circle cx="12" cy="18" r="2" /><path d="M7 6h10M6 8l5 8M18 8l-5 8" /></>),
  sun: I(<><circle cx="12" cy="12" r="4" /><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6L7 7M17 17l1.4 1.4M18.4 5.6L17 7M7 17l-1.4 1.4" /></>),
  chat: I(<><path d="M5 5h14a2 2 0 012 2v8a2 2 0 01-2 2h-7l-5 4v-4H5a2 2 0 01-2-2V7a2 2 0 012-2z" /></>),
};

const STEPS = [
  ["01", "Connect", "Sign in with Google. CommitIQ reads your mail and creates drafts. It never sends anything on its own."],
  ["02", "Understand", "Gemini works out who promised what, to whom, and by when, from emails and meeting transcripts."],
  ["03", "Act", "See what is urgent, resolve clashes, and review the follow-up drafts that are prepared for you."],
];
const FEATURES = [
  ["mail", "Commitment detection", "Finds promises in Gmail and meeting transcripts, and ignores duplicates of the same one."],
  ["clash", "Clash detection", "Warns when one person has two commitments competing for the same day or hour."],
  ["gauge", "Priority and trust", "Scores what matters most and how reliably each person follows through."],
  ["graph", "Dependency graph", "Shows which commitment blocks which, the critical path, and the cascade when something slips."],
  ["sun", "Daily and weekly brief", "A short summary of what needs attention, written for you every morning and every Monday."],
  ["chat", "Chat assistant", "A small window that opens when you minimise the dashboard, with quick replies for each new commitment."],
];

function Words({ text, accentFrom }) {
  const words = text.split(" ");
  return words.map((w, i) => (
    <span key={i}>
      <span className="w"><span className={accentFrom !== undefined && i >= accentFrom ? "cin-serif" : ""}>{w}</span></span>{" "}
    </span>
  ));
}

function TiltCard({ children, className = "" }) {
  const ref = useRef(null);
  useTilt(ref, { max: 7, lift: 8 });
  return <div ref={ref} className={`cin-card ${className}`}>{children}</div>;
}

function Magnetic({ children }) {
  const ref = useRef(null);
  useMagnetic(ref);
  return <span ref={ref} style={{ display: "inline-block" }}>{children}</span>;
}

export default function Landing({ go }) {
  const { googleUrl } = useAuth();
  const root = useRef(null), steps = useRef(null), feats = useRef(null), final = useRef(null);
  const [solid, setSolid] = useState(false);

  useEffect(() => {
    const on = () => setSolid(window.scrollY > 24);
    on(); window.addEventListener("scroll", on, { passive: true });
    return () => window.removeEventListener("scroll", on);
  }, []);

  // hero entrance
  useLayoutEffect(() => {
    if (reducedMotion()) return;
    const ctx = gsap.context(() => {
      const tl = gsap.timeline({ defaults: { ease: "power3.out" } });
      tl.from(".lp-nav-in", { y: -24, opacity: 0, duration: 0.9 })
        .from(".lp-hero .cin-eyebrow", { y: 16, opacity: 0, duration: 0.7 }, 0.15)
        .from(".lp-h1 .w > span", { yPercent: 115, duration: 1.1, stagger: 0.07 }, 0.25)
        .from(".lp-hero .cin-lead", { y: 20, opacity: 0, duration: 0.9 }, 0.85)
        .from(".lp-points li", { x: -16, opacity: 0, duration: 0.7, stagger: 0.1 }, 1.0)
        .from(".lp-cta > *", { y: 18, opacity: 0, duration: 0.8, stagger: 0.1 }, 1.25)
        .from(".lp-scroll", { opacity: 0, duration: 1 }, 1.8);
    }, root);
    return () => ctx.revert();
  }, []);

  useReveal(steps, ".lp-step", { stagger: 0.14 });
  useReveal(feats, ".lp-feat", { stagger: 0.08 });
  useReveal(final, ".lp-final-in > *", { stagger: 0.12 });

  return (
    <div className="lp cin-grain" ref={root}>
      <header className={`lp-nav ${solid ? "solid" : ""}`}>
        <div className="lp-nav-in">
          <Logo onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })} />
          <div className="lp-nav-actions">
            <button className="cin-btn ghost small" onClick={() => go("signin")}>Sign in</button>
            <button className="cin-btn primary small" onClick={() => go("signup")}>Get started</button>
          </div>
        </div>
      </header>

      <section className="lp-hero">
        <div className="lp-hero-bg" />
        <Suspense fallback={null}><Hero3D variant="hero" /></Suspense>
        <div className="cin-vignette" style={{ position: "absolute", inset: 0, pointerEvents: "none", zIndex: 1 }} />
        <div className="lp-wrap">
          <div className="lp-hero-copy">
            <span className="cin-eyebrow"><i />AI commitment intelligence</span>
            <h1 className="cin-h1 lp-h1"><Words text="Never lose track of what you promised." accentFrom={5} /></h1>
            <p className="cin-lead">
              CommitIQ reads your mail and meetings, finds every commitment people make, and keeps you ahead of the
              deadline. It prepares follow-ups as drafts and never sends anything on its own.
            </p>
            <ul className="lp-points">
              <li>Finds commitments in Gmail and meeting transcripts</li>
              <li>Flags clashes and ranks what is urgent</li>
              <li>Maps dependencies and writes your daily brief</li>
            </ul>
            <div className="lp-cta">
              <Magnetic><button className="cin-btn primary" onClick={() => go("signup")}>Create a free account</button></Magnetic>
              <Magnetic><button className="cin-btn ghost" onClick={() => go("signin")}>Sign in</button></Magnetic>
            </div>
          </div>
        </div>
        <div className="lp-scroll"><span>Scroll</span><i /></div>
      </section>

      <section className="lp-section" ref={steps}>
        <div className="lp-wrap">
          <div className="lp-head">
            <div className="lp-kicker">How it works</div>
            <h2 className="cin-h2">From inbox to <span className="cin-serif">clarity</span> in three steps.</h2>
          </div>
          <div className="lp-grid">
            {STEPS.map(([n, t, d]) => (
              <TiltCard key={n} className="lp-card lp-step"><div className="lp-num">{n}</div><h3>{t}</h3><p>{d}</p></TiltCard>
            ))}
          </div>
        </div>
      </section>

      <section className="lp-section" ref={feats}>
        <div className="lp-wrap">
          <div className="lp-head">
            <div className="lp-kicker">What you get</div>
            <h2 className="cin-h2">Everything in one calm, <span className="cin-serif">dark</span> dashboard.</h2>
            <p className="cin-lead">Built to be checked quickly and trusted completely: every email it writes is a draft you approve.</p>
          </div>
          <div className="lp-grid">
            {FEATURES.map(([ic, t, d]) => (
              <TiltCard key={t} className="lp-card lp-feat"><div className="lp-icon">{ICONS[ic]}</div><h3>{t}</h3><p>{d}</p></TiltCard>
            ))}
          </div>
        </div>
      </section>

      <section className="lp-section lp-final" ref={final}>
        <div className="lp-wrap lp-final-in">
          <h2 className="cin-h2">Start with your own <span className="cin-serif">inbox.</span></h2>
          <p className="cin-lead" style={{ maxWidth: "34rem", margin: "1rem auto 0" }}>
            Sign in with Google and CommitIQ begins finding commitments straight away.
          </p>
          <div className="lp-cta">
            <div style={{ width: "min(20rem, 100%)" }}><GoogleButton href={googleUrl} /></div>
            <button className="cin-btn ghost" onClick={() => go("signup")}>Use email instead</button>
          </div>
        </div>
      </section>

      <footer className="lp-foot">
        <div className="lp-wrap"><span>© {new Date().getFullYear()} CommitIQ</span><span>Mail is only read, and replies are only ever saved as drafts.</span></div>
      </footer>
    </div>
  );
}
