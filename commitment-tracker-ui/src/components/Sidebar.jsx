import { LogoMark } from "./Logo";
import { API } from "../auth/AuthContext";

const APP_NAME = "CommitIQ";
const APP_TAGLINE = "AI Powered Commitment intelligence";

const ico = (d) => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{d}</svg>
);
const NAV_ITEMS = [
  { key: "overview", label: "Overview", icon: ico(<><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></>) },
  { key: "commitments", label: "Commitments", icon: ico(<><path d="M9 6h11M9 12h11M9 18h11" /><path d="M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2" /></>) },
  { key: "dependencies", label: "Dependencies", icon: ico(<><circle cx="5" cy="6" r="2" /><circle cx="19" cy="6" r="2" /><circle cx="12" cy="18" r="2" /><path d="M7 6h10M6 8l5 8M18 8l-5 8" /></>) },
  { key: "brief", label: "AI brief", icon: ico(<><circle cx="12" cy="12" r="4" /><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6L7 7M17 17l1.4 1.4M18.4 5.6L17 7M7 17l-1.4 1.4" /></>) },
];

function Avatar({ user }) {
  if (user?.avatar_url) return <img src={user.avatar_url} alt="" referrerPolicy="no-referrer" style={{ width: 38, height: 38, borderRadius: "50%", objectFit: "cover" }} />;
  const initials = (user?.display_name || "?").split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
  return <span style={{ width: 38, height: 38, borderRadius: "50%", display: "grid", placeItems: "center", background: "var(--raised)", border: "1px solid var(--line-2)", font: "600 0.8rem var(--sans)" }}>{initials}</span>;
}

export default function Sidebar({ active, onNavigate, connected, user, onSignOut }) {
  return (
    <aside style={{ width: 252, flexShrink: 0, height: "100vh", position: "sticky", top: 0, display: "flex", flexDirection: "column",
      background: "linear-gradient(180deg, #0a0a0b, #060606)", borderRight: "1px solid var(--line)", color: "var(--ink)" }}>
      <div style={{ padding: "1.6rem 1.4rem 1.3rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: ".7rem" }}>
          <LogoMark size={34} />
          <span style={{ font: "700 1.1rem var(--sans)", letterSpacing: "-0.02em" }}>{APP_NAME}</span>
        </div>
        <p style={{ margin: ".7rem 0 0", color: "var(--faint)", fontSize: ".74rem", lineHeight: 1.4 }}>{APP_TAGLINE}</p>
      </div>

      <nav style={{ flex: 1, padding: "0.4rem 1.1rem", display: "grid", gap: ".25rem", alignContent: "start" }}>
        {NAV_ITEMS.map((item) => (
          <button key={item.key} onClick={() => onNavigate(item.key)} className={`cin-side-link ${active === item.key ? "active" : ""}`}>
            {item.icon}{item.label}
          </button>
        ))}
      </nav>

      <div style={{ padding: "1rem 1.1rem 1.2rem", borderTop: "1px solid var(--line)", display: "grid", gap: ".85rem" }}>
        
        <div style={{ display: "flex", alignItems: "center", gap: ".5rem", color: "var(--faint)", fontSize: ".76rem", padding: "0 .3rem" }}>
          <span style={{ width: 7, height: 7, borderRadius: "50%", background: connected ? "#34c77e" : "#ff4d57", boxShadow: connected ? "0 0 10px #34c77e" : "none" }} />
          {connected ? "Synced live" : "Reconnecting…"}
        </div>

        {user && (
          <div style={{ display: "flex", alignItems: "center", gap: ".7rem", padding: ".65rem", borderRadius: 14, background: "rgba(255,255,255,.04)", border: "1px solid var(--line)" }}>
            <Avatar user={user} />
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ font: "600 .84rem var(--sans)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{user.display_name}</div>
              {user.gmail_connected
                ? <div style={{ color: "#5fd9a0", fontSize: ".7rem" }}>Gmail connected</div>
                : <a href={`${API}/auth/google/start`} style={{ color: "var(--mute)", fontSize: ".7rem", textDecoration: "underline" }}>Connect Gmail</a>}
            </div>
            <button onClick={onSignOut} title="Sign out" aria-label="Sign out"
              style={{ border: 0, background: "none", color: "var(--mute)", cursor: "pointer", padding: ".35rem" }}>
              {ico(<><path d="M9 4H5a2 2 0 00-2 2v12a2 2 0 002 2h4" /><path d="M16 8l4 4-4 4M20 12H9" /></>)}
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}
