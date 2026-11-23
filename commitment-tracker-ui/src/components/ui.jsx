export const STATUS_STYLES = {
  open: { bg: "bg-blue-50", text: "text-blue-700", dot: "bg-blue-500" },
  at_risk: { bg: "bg-amber-50", text: "text-amber-700", dot: "bg-amber-500" },
  overdue: { bg: "bg-red-50", text: "text-red-700", dot: "bg-red-500" },
  done: { bg: "bg-emerald-50", text: "text-emerald-700", dot: "bg-emerald-500" },
  cancelled: { bg: "bg-stone-100", text: "text-stone-500", dot: "bg-stone-400" },
};

export function StatusBadge({ status }) {
  const s = STATUS_STYLES[status] || STATUS_STYLES.open;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${s.bg} ${s.text}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
      {status.replace("_", " ")}
    </span>
  );
}

export function StatCard({ label, value, accent }) {
  return (
    <div className="bg-white rounded-2xl border border-stone-200 px-5 py-4 flex-1">
      <p className="text-xs font-medium text-stone-500 uppercase tracking-wide">{label}</p>
      <p className={`text-2xl font-semibold mt-1 ${accent || "text-stone-900"}`}>{value}</p>
    </div>
  );
}

export function Toast({ message, onDismiss, isNudge }) {
  return (
    <div className="bg-stone-900 text-white rounded-xl px-4 py-3 shadow-lg flex items-start gap-3 min-w-[280px] animate-[slideIn_0.2s_ease-out]">
      <span className="text-lg">{isNudge ? "✉️" : "📬"}</span>
      <div className="flex-1">
        <p className="text-sm font-medium">{isNudge ? "Draft ready for review" : "New commitment detected"}</p>
        <p className="text-sm text-stone-300 mt-0.5">{message}</p>
        {isNudge && (
          <a
            href="https://mail.google.com/mail/u/0/#drafts"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-amber-300 hover:text-amber-200 underline mt-1 inline-block"
          >
            Open Gmail Drafts →
          </a>
        )}
      </div>
      <button onClick={onDismiss} className="text-stone-400 hover:text-white text-sm">✕</button>
    </div>
  );
}

// Backend returns naive local datetimes like "2026-09-25T18:00:00".
// Midnight means "no time mentioned" (a date-only deadline).
function hasTimeOfDay(date) {
  return date.getHours() !== 0 || date.getMinutes() !== 0;
}

export function formatDate(dateStr) {
  if (!dateStr) return "No deadline";
  const date = new Date(dateStr);
  const datePart = date.toLocaleDateString(undefined, {
    month: "short", day: "numeric", year: "numeric",
  });
  if (!hasTimeOfDay(date)) return datePart;
  const timePart = date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return `${datePart}, ${timePart}`;
}

// The moment a commitment actually becomes late. Date-only deadlines count
// as the END of that day, so "due Sep 25" isn't overdue at 12:01 AM on the 25th.
export function deadlineOf(dueDateStr) {
  const d = new Date(dueDateStr);
  if (!hasTimeOfDay(d)) d.setHours(23, 59, 59, 0);
  return d;
}

export function isOverdue(c) {
  return !!c.due_date
    && (c.status === "open" || c.status === "at_risk")
    && deadlineOf(c.due_date) < new Date();
}

export function timeAgo(isoString) {
  const seconds = Math.floor((new Date() - new Date(isoString)) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function gmailDraftsUrl() {
  return "https://mail.google.com/mail/u/0/#drafts";
}

// Score bands for the 0-100 priority score computed by the backend.
export function priorityLevel(score) {
  const s = score ?? 0;
  if (s >= 75) return { key: "critical", label: "Critical", classes: "bg-red-600 text-white" };
  if (s >= 50) return { key: "high", label: "High", classes: "bg-orange-100 text-orange-800" };
  if (s >= 25) return { key: "medium", label: "Medium", classes: "bg-yellow-100 text-yellow-800" };
  return { key: "low", label: "Low", classes: "bg-stone-100 text-stone-600" };
}

export function PriorityBadge({ score, reason }) {
  // Done / cancelled items score 0 - show a dash instead of a misleading "Low".
  if (!score || score <= 0) {
    return <span className="text-stone-300 text-sm">—</span>;
  }
  const level = priorityLevel(score);
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold ${level.classes}`}
      title={reason || `Priority score ${score}`}
    >
      {level.label}
      <span className="opacity-70 font-medium">{Math.round(score)}</span>
    </span>
  );
}