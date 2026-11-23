import { useState } from "react";
import {
  StatusBadge, PriorityBadge, STATUS_STYLES, formatDate, isOverdue, deadlineOf,
} from "../components/ui";

const API_BASE = "http://127.0.0.1:8000";
const FILTERS = ["all", "open", "at_risk", "overdue", "done", "clashes"];

const dueMs = (c) => (c.due_date ? deadlineOf(c.due_date).getTime() : Number.MAX_SAFE_INTEGER);
const createdMs = (c) => new Date(c.created_at).getTime();

const SORTS = {
  priority: {
    label: "Priority",
    fn: (a, b) => (b.priority_score ?? 0) - (a.priority_score ?? 0) || createdMs(b) - createdMs(a),
  },
  due: { label: "Due date", fn: (a, b) => dueMs(a) - dueMs(b) || createdMs(b) - createdMs(a) },
  newest: { label: "Newest", fn: (a, b) => createdMs(b) - createdMs(a) },
};

// Backend returns naive ISO strings like "2026-09-25T18:00:00".
// Midnight means "no time mentioned", so we show an empty time field for it.
function splitDueDate(dueDate) {
  if (!dueDate) return { date: "", time: "" };
  const date = dueDate.slice(0, 10);
  const time = dueDate.length >= 16 ? dueDate.slice(11, 16) : "";
  return { date, time: time === "00:00" ? "" : time };
}

const hasTime = (c) => splitDueDate(c.due_date).time !== "";

// Adds a meeting to Google Calendar. If you have a clash the server answers 409
// "busy" and we ask before overriding. Returns "scheduled" | "cancelled" | "error".
export async function scheduleMeeting(id, { apiBase = API_BASE, fetchFn = fetch, confirmFn = (m) => window.confirm(m), alertFn = (m) => window.alert(m) } = {}) {
  const url = `${apiBase}/commitments/${id}/schedule`;
  let res = await fetchFn(url, { method: "POST" });

  if (res.status === 409) {
    const body = await res.json().catch(() => ({}));
    if (body?.detail?.reason === "busy") {
      if (!confirmFn("You're busy at that time. Add it to your calendar anyway?")) return "cancelled";
      res = await fetchFn(`${url}?force=true`, { method: "POST" });
    } else {
      alertFn(typeof body?.detail === "string" ? body.detail : "This meeting is already scheduled.");
      return "error";
    }
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    alertFn(typeof body?.detail === "string" ? body.detail : "Couldn't schedule the meeting.");
    return "error";
  }
  return "scheduled";
}

function MeetingBadges({ c, onDeleteDraft }) {
  const status = c.calendar_status;
  return (
    <>
      {status === "scheduled" ? (
        <span className="inline-flex items-center gap-1.5 text-xs text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-full">
          <a href={c.calendar_event_url} target="_blank" rel="noopener noreferrer"
            className="hover:underline" title="Open in Google Calendar">
            📅 On your calendar
          </a>
          {c.meet_url && (
            <a href={c.meet_url} target="_blank" rel="noopener noreferrer"
              className="hover:underline" title="Google Meet link">
              · Meet
            </a>
          )}
        </span>
      ) : status === "free" ? (
        <span className="text-xs text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-full"
          title="Nothing else on your calendar at that time">
          📅 You're free
        </span>
      ) : status === "busy" ? (
        <span className="text-xs text-red-700 bg-red-50 px-2 py-0.5 rounded-full"
          title="You have something else at that time - alternatives were offered in the email draft">
          ⚠ Calendar conflict
        </span>
      ) : status === "removed" ? (
        <span className="text-xs text-stone-500 bg-stone-100 px-2 py-0.5 rounded-full">📅 Not on calendar</span>
      ) : (
        <span className="text-xs text-stone-500 bg-stone-100 px-2 py-0.5 rounded-full"
          title={hasTime(c) ? "Detected as a meeting" : "A meeting needs a time before it can be checked or scheduled"}>
          {hasTime(c) ? "📅 Meeting" : "📅 Meeting · add a time"}
        </span>
      )}
      {c.meeting_draft_id && (
        <span className="inline-flex items-center gap-1 text-xs text-amber-700 bg-amber-50 px-2 py-0.5 rounded-full">
          <a href="https://mail.google.com/mail/u/0/#drafts" target="_blank" rel="noopener noreferrer"
            className="hover:underline" title="Review the availability email in Gmail">
            ✉️ Availability email
          </a>
          <button onClick={() => onDeleteDraft(c.id)}
            className="text-amber-700 hover:text-red-600 font-bold leading-none"
            title="Delete this availability email draft">
            ×
          </button>
        </span>
      )}
    </>
  );
}

function EditableRow({ commitment, onSave, onCancel }) {
  const { date, time } = splitDueDate(commitment.due_date);
  const [form, setForm] = useState({
    owner: commitment.owner,
    beneficiary: commitment.beneficiary || "",
    description: commitment.description,
    due_date: date,
    due_time: time,
    status: commitment.status,
    is_important: commitment.is_important || false,
  });

  return (
    <tr className="bg-amber-50/50">
      <td className="p-3">
        <input className="border border-stone-300 rounded-lg px-2.5 py-1.5 w-full text-sm focus:outline-none focus:ring-2 focus:ring-stone-900"
          value={form.owner} onChange={(e) => setForm({ ...form, owner: e.target.value })} />
      </td>
      <td className="p-3">
        <input className="border border-stone-300 rounded-lg px-2.5 py-1.5 w-full text-sm focus:outline-none focus:ring-2 focus:ring-stone-900"
          value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
      </td>
      <td className="p-3">
        <input type="date" className="border border-stone-300 rounded-lg px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-stone-900"
          value={form.due_date} onChange={(e) => setForm({ ...form, due_date: e.target.value })} />
        <input type="time" className="block mt-1.5 border border-stone-300 rounded-lg px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-stone-900"
          value={form.due_time} onChange={(e) => setForm({ ...form, due_time: e.target.value })} />
      </td>
      <td className="p-3 text-stone-300 text-sm">—</td>
      <td className="p-3">
        <select className="border border-stone-300 rounded-lg px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-stone-900"
          value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
          {Object.keys(STATUS_STYLES).map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <label className="flex items-center gap-1.5 mt-2 text-xs text-stone-600">
          <input type="checkbox" checked={form.is_important}
            onChange={(e) => setForm({ ...form, is_important: e.target.checked })} />
          Important
        </label>
      </td>
      <td className="p-3">
        <div className="flex gap-2">
          <button onClick={() => onSave(commitment.id, form)}
            className="bg-stone-900 text-white text-sm px-3 py-1.5 rounded-lg hover:bg-stone-700 transition">
            Save
          </button>
          <button onClick={onCancel}
            className="text-sm px-3 py-1.5 rounded-lg border border-stone-300 hover:bg-stone-100 transition">
            Cancel
          </button>
        </div>
      </td>
    </tr>
  );
}

export default function Commitments({ commitments, onSaved }) {
  const [editingId, setEditingId] = useState(null);
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState("priority");
  const [syncing, setSyncing] = useState(false);

  const handleSave = async (id, form) => {
    const { due_time, ...rest } = form;
    const payload = {
      ...rest,
      beneficiary: rest.beneficiary || null,
      // Sent as a naive local datetime (no "Z") so the exact time typed is
      // what gets stored. No time chosen -> midnight, meaning "date only".
      due_date: rest.due_date ? `${rest.due_date}T${due_time || "00:00"}:00` : null,
    };
    await fetch(`${API_BASE}/commitments/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    setEditingId(null);
    onSaved?.();
  };

  const handleDelete = async (id) => {
    if (!window.confirm("Delete this commitment? This can't be undone.")) return;
    await fetch(`${API_BASE}/commitments/${id}`, { method: "DELETE" });
    onSaved?.();
  };

  const handleSchedule = async (id) => {
    await scheduleMeeting(id);
    onSaved?.();
  };

  const handleUnschedule = async (id) => {
    if (!window.confirm("Remove this meeting from your Google Calendar?")) return;
    const res = await fetch(`${API_BASE}/commitments/${id}/event`, { method: "DELETE" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      window.alert(typeof body?.detail === "string" ? body.detail : "Couldn't remove the event.");
    }
    onSaved?.();
  };

  const handleDeleteMeetingDraft = async (id) => {
    if (!window.confirm("Delete this availability email draft from Gmail?")) return;
    await fetch(`${API_BASE}/commitments/${id}/meeting-draft`, { method: "DELETE" });
    onSaved?.();
  };

  const handleDeleteDraft = async (id) => {
    if (!window.confirm("Delete this draft from Gmail?")) return;
    await fetch(`${API_BASE}/commitments/${id}/draft`, { method: "DELETE" });
    onSaved?.();
  };

  const handleSyncNow = async () => {
    setSyncing(true);
    try {
      await fetch(`${API_BASE}/ingest/run-now`, { method: "POST" });
    } finally {
      setSyncing(false);
    }
  };

  const filtered = commitments.filter((c) => {
    const matchesFilter =
      filter === "all" ? true :
      filter === "clashes" ? !!c.clash_group_id :
      c.status === filter;
    const matchesSearch =
      c.owner.toLowerCase().includes(search.toLowerCase()) ||
      c.description.toLowerCase().includes(search.toLowerCase());
    return matchesFilter && matchesSearch;
  }).sort(SORTS[sortBy].fn);

  return (
    <div>
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-semibold text-stone-900 tracking-tight">Commitments</h1>
          <p className="text-stone-500 text-sm mt-1">Tracked automatically from your inbox</p>
        </div>
        <button
          onClick={handleSyncNow}
          disabled={syncing}
          className="text-sm px-3.5 py-2 rounded-lg border border-stone-300 bg-white hover:bg-stone-100 transition disabled:opacity-50"
        >
          {syncing ? "Syncing…" : "Sync now"}
        </button>
      </div>

      <div className="flex items-center justify-between mb-4 gap-4">
        <div className="flex gap-1.5">
          {FILTERS.map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-3.5 py-1.5 rounded-full text-sm font-medium capitalize transition ${
                filter === f
                  ? "bg-stone-900 text-white"
                  : "bg-white text-stone-600 border border-stone-200 hover:bg-stone-100"
              }`}
            >
              {f.replace("_", " ")}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-stone-500">
            Sort
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value)}
              className="border border-stone-300 rounded-lg px-2.5 py-1.5 text-sm text-stone-800 bg-white focus:outline-none focus:ring-2 focus:ring-stone-900"
            >
              {Object.entries(SORTS).map(([key, s]) => (
                <option key={key} value={key}>{s.label}</option>
              ))}
            </select>
          </label>
          <input
            type="text"
            placeholder="Search commitments..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="border border-stone-300 rounded-lg px-3.5 py-1.5 text-sm w-64 focus:outline-none focus:ring-2 focus:ring-stone-900"
          />
        </div>
      </div>

      <div className="bg-white rounded-2xl border border-stone-200 overflow-hidden">
        <table className="w-full text-left">
          <thead className="bg-stone-50 border-b border-stone-200">
            <tr>
              <th className="p-3.5 text-xs font-semibold text-stone-500 uppercase tracking-wide">Owner</th>
              <th className="p-3.5 text-xs font-semibold text-stone-500 uppercase tracking-wide">Description</th>
              <th className="p-3.5 text-xs font-semibold text-stone-500 uppercase tracking-wide">Due</th>
              <th className="p-3.5 text-xs font-semibold text-stone-500 uppercase tracking-wide">Priority</th>
              <th className="p-3.5 text-xs font-semibold text-stone-500 uppercase tracking-wide">Status</th>
              <th className="p-3.5"></th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr><td colSpan="6" className="p-10 text-center text-stone-400 text-sm">
                {commitments.length === 0 ? "No commitments yet — waiting on the next Gmail sync." : "No commitments match your filters."}
              </td></tr>
            )}
            {filtered.map((c) =>
              editingId === c.id ? (
                <EditableRow key={c.id} commitment={c} onSave={handleSave} onCancel={() => setEditingId(null)} />
              ) : (
                <tr key={c.id} className="border-b border-stone-100 last:border-0 hover:bg-stone-50 transition">
                  <td className="p-3.5 text-sm text-stone-800 font-medium">{c.owner}</td>
                  <td className="p-3.5 text-sm text-stone-700">{c.description}</td>
                  <td className={`p-3.5 text-sm ${isOverdue(c) ? "text-red-600 font-medium" : "text-stone-500"}`}>
                    {formatDate(c.due_date)}
                  </td>
                  <td className="p-3.5">
                    <PriorityBadge score={c.priority_score} reason={c.priority_reason} />
                  </td>
                  <td className="p-3.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge status={isOverdue(c) ? "overdue" : c.status} />
                      {c.clash_group_id && (
                        <span className="text-xs text-red-700 bg-red-50 px-2 py-0.5 rounded-full"
                          title="Clashes with another commitment (overlapping time, or same day for date-only items)">
                          ⚠ Clash
                        </span>
                      )}
                      {c.is_important && (
                        <span className="text-xs text-purple-700 bg-purple-50 px-2 py-0.5 rounded-full"
                          title="Marked important - won't get an auto-drafted reschedule apology">
                          ⭐ Important
                        </span>
                      )}
                      {c.gmail_draft_id && (
                        <span className="inline-flex items-center gap-1 text-xs text-amber-700 bg-amber-50 px-2 py-0.5 rounded-full">
                          <a href="https://mail.google.com/mail/u/0/#drafts" target="_blank" rel="noopener noreferrer"
                            className="hover:underline" title="Review in Gmail">
                            ✉️ Draft ready
                          </a>
                          <button onClick={() => handleDeleteDraft(c.id)}
                            className="text-amber-700 hover:text-red-600 font-bold leading-none"
                            title="Delete this draft">
                            ×
                          </button>
                        </span>
                      )}
                      {c.is_meeting && <MeetingBadges c={c} onDeleteDraft={handleDeleteMeetingDraft} />}
                    </div>
                  </td>
                  <td className="p-3.5">
                    <div className="flex items-center gap-3">
                      {c.is_meeting && hasTime(c) && c.status !== "done" && c.status !== "cancelled" && c.calendar_status !== "scheduled" && (
                        <button onClick={() => handleSchedule(c.id)}
                          className="text-sm text-emerald-700 hover:text-emerald-900 font-medium transition"
                          title="Add this meeting to your Google Calendar with a Meet link">
                          Schedule
                        </button>
                      )}
                      {c.calendar_status === "scheduled" && (
                        <button onClick={() => handleUnschedule(c.id)}
                          className="text-sm text-stone-500 hover:text-stone-900 font-medium transition"
                          title="Remove from your Google Calendar">
                          Unschedule
                        </button>
                      )}
                      <button onClick={() => setEditingId(c.id)}
                        className="text-sm text-stone-500 hover:text-stone-900 font-medium transition">
                        Edit
                      </button>
                      <button onClick={() => handleDelete(c.id)}
                        className="text-sm text-red-500 hover:text-red-700 font-medium transition">
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              )
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}