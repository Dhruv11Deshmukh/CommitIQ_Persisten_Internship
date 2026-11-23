import { useEffect, useState } from "react";
import {
  StatCard, StatusBadge, PriorityBadge, formatDate, isOverdue, deadlineOf, timeAgo,
} from "../components/ui";
import { BriefCard } from "./Brief";

const API_BASE = "http://127.0.0.1:8000";

const EVENT_LABELS = {
  created: "New commitment detected",
  edited: "Commitment edited",
  merged: "Duplicates merged",
  nudged: "Follow-up drafted",
  auto_verified: "Marked done automatically",
  clash_conflict_drafted: "Conflict notice drafted",
  reschedule_apology_drafted: "Reschedule apology drafted",
  draft_auto_deleted: "Stale draft removed",
  draft_deleted: "Draft deleted",
  duplicate_skipped: "Duplicate skipped",
  deleted: "Commitment deleted",
  meeting_checked: "Calendar checked, email drafted",
  meeting_scheduled: "Meeting added to calendar",
  meeting_unscheduled: "Meeting removed from calendar",
  meeting_rescheduled: "Calendar event moved",
  meeting_draft_deleted: "Availability draft deleted",
};

const TRUST_STYLES = {
  Reliable: "bg-emerald-50 text-emerald-700",
  Fair: "bg-yellow-50 text-yellow-700",
  Unreliable: "bg-red-50 text-red-700",
  New: "bg-stone-100 text-stone-500",
};

export function PeopleCard({ people }) {
  return (
    <div className="bg-white rounded-2xl border border-stone-200 overflow-hidden mb-6">
      <div className="px-5 py-4 border-b border-stone-200">
        <h2 className="text-sm font-semibold">People</h2>
        <p className="text-xs text-stone-500 mt-0.5">
          How reliably each person follows through. A poor track record raises priority.
        </p>
      </div>
      <div className="divide-y divide-stone-100">
        {people.length === 0 && (
          <p className="p-6 text-sm text-stone-400 text-center">
            No history yet — scores appear once commitments reach their deadlines.
          </p>
        )}
        {people.slice(0, 6).map((p) => (
          <div key={p.person} className="px-5 py-3.5 flex items-center justify-between gap-4">
            <div className="min-w-0">
              <p className="text-sm font-medium text-stone-800 truncate">{p.person}</p>
              <p className="text-xs text-stone-500 mt-0.5">
                {p.on_time} on time · {p.late} late · {p.missed} missed · {p.open} open
              </p>
            </div>
            <div className="flex items-center gap-3 shrink-0">
              {p.label !== "New" && (
                <span className="text-sm font-semibold text-stone-700">{p.score}%</span>
              )}
              <span
                className={`px-2.5 py-1 rounded-full text-xs font-medium ${TRUST_STYLES[p.label] || TRUST_STYLES.New}`}
                title={p.label === "New" ? "Needs 3 finished or missed deadlines before trust counts" : `Based on ${p.samples} deadlines`}
              >
                {p.label}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// Highest priority score first; ties go to the earlier deadline.
function rankByPriority(commitments) {
  const dueMs = (c) => (c.due_date ? deadlineOf(c.due_date).getTime() : Number.MAX_SAFE_INTEGER);
  return [...commitments]
    .filter((c) => c.status !== "done" && c.status !== "cancelled")
    .sort((a, b) => (b.priority_score ?? 0) - (a.priority_score ?? 0) || dueMs(a) - dueMs(b));
}

function PriorityRow({ c, onNavigate }) {
  return (
    <button
      onClick={() => onNavigate("commitments")}
      className="w-full text-left px-5 py-3.5 flex items-center justify-between gap-4 hover:bg-stone-50 transition"
    >
      <div className="min-w-0">
        <p className="text-sm font-medium text-stone-800 truncate">{c.description}</p>
        <p className="text-xs text-stone-500 mt-0.5 truncate">
          {c.owner}
          {c.priority_reason ? ` · ${c.priority_reason}` : ""}
        </p>
      </div>
      <div className="flex items-center gap-3 shrink-0">
        <span className={`text-sm ${isOverdue(c) ? "text-red-600 font-semibold" : "text-stone-500"}`}>
          {formatDate(c.due_date)}
        </span>
        <PriorityBadge score={c.priority_score} reason={c.priority_reason} />
        <StatusBadge status={isOverdue(c) ? "overdue" : c.status} />
      </div>
    </button>
  );
}

export default function Overview({ commitments, refreshSignal, onNavigate }) {
  const [activity, setActivity] = useState([]);
  const [people, setPeople] = useState([]);

  useEffect(() => {
    fetch(`${API_BASE}/activity`)
      .then((res) => res.json())
      .then(setActivity)
      .catch((err) => console.error("Failed to load activity:", err));

    fetch(`${API_BASE}/people`)
      .then((res) => res.json())
      .then(setPeople)
      .catch((err) => console.error("Failed to load people:", err));
  }, [refreshSignal]);

  const counts = {
    open: commitments.filter((c) => c.status === "open").length,
    overdue: commitments.filter((c) => c.status === "overdue" || isOverdue(c)).length,
    done: commitments.filter((c) => c.status === "done").length,
    total: commitments.length,
  };

  const priorityList = rankByPriority(commitments).slice(0, 6);

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight">Overview</h1>
        <p className="text-stone-500 text-sm mt-1">What needs your attention right now, most urgent first.</p>
      </div>

      <div className="grid grid-cols-4 gap-4 mb-8">
        <StatCard label="Total" value={counts.total} />
        <StatCard label="Open" value={counts.open} accent="text-blue-700" />
        <StatCard label="Overdue" value={counts.overdue} accent="text-red-700" />
        <StatCard label="Completed" value={counts.done} accent="text-emerald-700" />
      </div>

      <div className="bg-white rounded-2xl border border-stone-200 overflow-hidden mb-6">
        <div className="px-5 py-4 border-b border-stone-200 flex items-center justify-between">
          <h2 className="text-sm font-semibold">Needs attention</h2>
          <button
            onClick={() => onNavigate("commitments")}
            className="text-xs text-stone-500 hover:text-stone-900 font-medium transition"
          >
            View all →
          </button>
        </div>
        <div className="divide-y divide-stone-100">
          {priorityList.length === 0 && (
            <p className="p-6 text-sm text-stone-400 text-center">Nothing urgent — you're all caught up.</p>
          )}
          {priorityList.map((c) => (
            <PriorityRow key={c.id} c={c} onNavigate={onNavigate} />
          ))}
        </div>
      </div>

      <PeopleCard people={people} />

      <div className="bg-white rounded-2xl border border-stone-200 overflow-hidden">
        <div className="px-5 py-3.5 border-b border-stone-200">
          <h2 className="text-xs font-semibold text-stone-500 uppercase tracking-wide">Recent activity</h2>
        </div>
        <div className="divide-y divide-stone-100">
          {activity.length === 0 && (
            <p className="p-5 text-xs text-stone-400 text-center">No activity yet.</p>
          )}
          {activity.slice(0, 5).map((a) => (
            <div key={a.id} className="px-5 py-2.5 flex items-center justify-between gap-4">
              <p className="text-xs text-stone-600">{EVENT_LABELS[a.event] || a.event}</p>
              <span className="text-xs text-stone-400 whitespace-nowrap">{timeAgo(a.timestamp)}</span>
            </div>
          ))}
        </div>
      </div>

      <BriefCard compact />
    </div>
  );
}