// Dark toast used by the new dashboard. Lives in its own file so your existing ui.jsx stays untouched.
export default function Toast({ message, onDismiss, isNudge }) {
  return (
    <div className="cin-toast animate-[slideIn_0.2s_ease-out]">
      <span className="text-lg">{isNudge ? "✉️" : "📬"}</span>
      <div className="flex-1">
        <p style={{ margin: 0, fontWeight: 600, fontSize: ".9rem" }}>{isNudge ? "Draft ready for review" : "New commitment detected"}</p>
        <p style={{ margin: ".2rem 0 0", color: "var(--mute)", fontSize: ".86rem" }}>{message}</p>
        {isNudge && (
          <a
            href="https://mail.google.com/mail/u/0/#drafts"
            target="_blank"
            rel="noopener noreferrer"
            style={{ display: "inline-block", marginTop: ".4rem", color: "var(--ink)", fontSize: ".8rem", textDecoration: "underline" }}
          >
            Open Gmail Drafts →
          </a>
        )}
      </div>
      <button onClick={onDismiss} style={{ border: 0, background: "none", color: "var(--mute)", cursor: "pointer" }} aria-label="Dismiss">✕</button>
    </div>
  );
}