"use client";

/* Monthly calendar and posting schedule (Section 3.4). */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Download, Eye, Play, SkipForward, Sparkles } from "lucide-react";
import { http } from "@/lib/api";
import type { Cycle } from "@/lib/types";
import { EmptyState, Modal, StatusChip, fmtDate, useToast } from "@/components/ui";

const CHIP_BG: Record<string, string> = {
  PLANNED: "border-slate-200 bg-white",
  GENERATING: "border-sky-300 bg-sky-50",
  AWAITING_REVIEW: "border-amber-300 bg-amber-50",
  SCHEDULED: "border-primary-300 bg-primary-50",
  PUBLISHED: "border-emerald-300 bg-emerald-50",
  SKIPPED: "border-slate-200 bg-slate-50 opacity-60",
  GENERATION_FAILED: "border-red-300 bg-red-50",
  NEEDS_ATTENTION: "border-red-300 bg-red-50",
};

const STATUS_COLOR: Record<string, string> = {
  PLANNED: "bg-slate-400", GENERATING: "bg-sky-500",
  AWAITING_REVIEW: "bg-amber-500", SCHEDULED: "bg-primary-600",
  PUBLISHED: "bg-emerald-500", SKIPPED: "bg-slate-300",
  GENERATION_FAILED: "bg-red-500", NEEDS_ATTENTION: "bg-red-500",
};

export default function CalendarPage() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const today = new Date();
  const [cursor, setCursor] = useState(new Date(today.getFullYear(), today.getMonth(), 1));
  const [detail, setDetail] = useState<Cycle | null>(null);
  const [reschedule, setReschedule] = useState<string>("");

  const { data: cycles = [], isLoading } = useQuery({
    queryKey: ["cycles", "all"],
    queryFn: () => http.get<Cycle[]>("/cycles"),
  });

  const mutate = useMutation({
    mutationFn: ({ path, body, method }: { path: string; body?: unknown; method?: string }) =>
      method === "PATCH" ? http.patch(path, body) : http.post(path, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["cycles"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      setDetail(null);
    },
    onError: (err: Error) => toast(err.message, "error"),
  });

  const byDate = useMemo(() => {
    const map = new Map<string, Cycle[]>();
    cycles.forEach((c) => {
      const key = new Date(c.post_at).toDateString();
      map.set(key, [...(map.get(key) ?? []), c]);
    });
    return map;
  }, [cycles]);

  const year = cursor.getFullYear();
  const month = cursor.getMonth();
  const firstWeekday = (new Date(year, month, 1).getDay() + 6) % 7; // Monday-first
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const cells = [
    ...Array.from({ length: firstWeekday }, () => null),
    ...Array.from({ length: daysInMonth }, (_, i) => i + 1),
  ];

  const downloadIcs = async () => {
    try {
      const { url } = await http.get<{ token: string; url: string }>("/calendar-token");
      const resp = await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000/api/v1"}${url}`);
      const text = await resp.text();
      const blob = new Blob([text], { type: "text/calendar" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "monthlymuse.ics";
      a.click();
      URL.revokeObjectURL(a.href);
      toast("Calendar file downloaded - posting days now appear in your own calendar.");
    } catch {
      toast("Could not download the calendar feed", "error");
    }
  };

  return (
    <div className="space-y-5 animate-fade">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-display">Calendar</h1>
          <p className="text-body text-muted mt-1">Past, current and upcoming cycles with statuses.</p>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn-secondary btn-sm" onClick={() => setCursor(new Date(year, month - 1, 1))} aria-label="Previous month">‹</button>
          <span className="text-body font-medium min-w-36 text-center">
            {cursor.toLocaleDateString("en-GB", { month: "long", year: "numeric" })}
          </span>
          <button className="btn-secondary btn-sm" onClick={() => setCursor(new Date(year, month + 1, 1))} aria-label="Next month">›</button>
          <button className="btn-secondary btn-sm" onClick={() => void downloadIcs()}>
            <Download className="w-4 h-4" /> .ics
          </button>
        </div>
      </div>

      {/* legend */}
      <div className="flex flex-wrap gap-3">
        {Object.entries(STATUS_COLOR).map(([status, color]) => (
          <span key={status} className="flex items-center gap-1.5 text-small text-muted">
            <span className={`w-2.5 h-2.5 rounded-full ${color}`} /> {status.replace(/_/g, " ")}
          </span>
        ))}
      </div>

      {isLoading ? (
        <div className="card p-4 animate-pulse h-96 bg-slate-50" />
      ) : cycles.length === 0 ? (
        <EmptyState title="No cycles yet"
          subtitle="Create a Monthly Plan and the next 12 cycles appear here automatically."
          action={<a className="btn-primary" href="/plans">Create a plan</a>} />
      ) : (
        <div className="card p-3 sm:p-4 overflow-x-auto">
          <div className="grid grid-cols-7 gap-1.5 min-w-[640px]">
            {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => (
              <div key={d} className="text-small text-muted text-center py-1 font-medium">{d}</div>
            ))}
            {cells.map((day, i) => {
              if (day === null) return <div key={`e${i}`} />;
              const date = new Date(year, month, day);
              const isToday = date.toDateString() === today.toDateString();
              const dayCycles = byDate.get(date.toDateString()) ?? [];
              return (
                <div key={day} className={`min-h-24 rounded-input border p-1.5 flex flex-col gap-1
                  ${isToday ? "border-primary-400 ring-1 ring-primary-200" : "border-line"}`}>
                  <span className={`text-small ${isToday ? "text-primary-700 font-semibold" : "text-muted"}`}>
                    {day}
                  </span>
                  {dayCycles.map((c) => (
                    <button key={c.id}
                      className={`text-left rounded-md border px-1.5 py-1 text-small transition-shadow hover:shadow-card ${CHIP_BG[c.status] ?? ""}`}
                      onClick={() => setDetail(c)}>
                      <span className="flex items-center gap-1">
                        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${STATUS_COLOR[c.status] ?? "bg-slate-300"}`} />
                        <span className="truncate">{c.plan_name ?? "post"}</span>
                      </span>
                    </button>
                  ))}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* upcoming list (timeline alternative) */}
      <div className="card p-5">
        <h3 className="text-h3 mb-3">Upcoming</h3>
        <ul className="space-y-3">
          {cycles.filter((c) => c.status !== "PUBLISHED" && c.status !== "SKIPPED").slice(0, 8).map((c) => (
            <li key={c.id} className="flex flex-wrap items-center gap-3 text-body">
              <CalendarClock className="w-4 h-4 text-muted" />
              <span className="font-medium">{fmtDate(c.post_at, { month: "long", day: "numeric" })}</span>
              <span className="text-muted">{c.plan_name}</span>
              <span className="text-small text-muted ml-auto hidden sm:block">
                generates {fmtDate(c.generate_at)}
              </span>
              <StatusChip status={c.status} />
              <button className="btn-secondary btn-sm" onClick={() => setDetail(c)}>Manage</button>
            </li>
          ))}
        </ul>
      </div>

      {/* cycle detail */}
      <Modal open={!!detail} onClose={() => setDetail(null)} title={detail ? `${fmtDate(detail.post_at, { month: "long", year: "numeric" })} cycle` : ""}>
        {detail && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-body">{detail.plan_name}</span>
              <StatusChip status={detail.status} />
            </div>
            <p className="text-body text-muted">
              Posts {fmtDate(detail.post_at)} · generates {fmtDate(detail.generate_at)}
            </p>

            <div className="flex flex-wrap gap-2">
              {(detail.status === "PLANNED" || detail.status === "GENERATION_FAILED") && (
                <button className="btn-primary btn-sm"
                  onClick={() => mutate.mutate({ path: `/cycles/${detail.id}/generate` })}>
                  <Sparkles className="w-4 h-4" /> Generate now
                </button>
              )}
              {detail.status === "AWAITING_REVIEW" && detail.latest_request_id && (
                <a className="btn-primary btn-sm" href={`/review/${detail.latest_request_id}`}>
                  <Eye className="w-4 h-4" /> Review candidates
                </a>
              )}
              {detail.status === "PLANNED" && (
                <button className="btn-secondary btn-sm"
                  onClick={() => mutate.mutate({ path: `/cycles/${detail.id}`, body: { status: "SKIPPED" }, method: "PATCH" })}>
                  <SkipForward className="w-4 h-4" /> Skip this month
                </button>
              )}
              {detail.status === "SKIPPED" && (
                <button className="btn-secondary btn-sm"
                  onClick={() => mutate.mutate({ path: `/cycles/${detail.id}`, body: { status: "PLANNED" }, method: "PATCH" })}>
                  <Play className="w-4 h-4" /> Re-open
                </button>
              )}
            </div>

            <div className="pt-3 border-t border-line">
              <label className="label" htmlFor="reschedule-date">Reschedule posting date</label>
              <div className="flex gap-2">
                <input id="reschedule-date" type="date" className="input" value={reschedule}
                  onChange={(e) => setReschedule(e.target.value)} />
                <button className="btn-secondary shrink-0" disabled={!reschedule}
                  onClick={() => {
                    mutate.mutate({
                      path: `/cycles/${detail.id}`,
                      body: { post_at: new Date(`${reschedule}T10:00:00Z`).toISOString() },
                      method: "PATCH",
                    });
                    setReschedule("");
                  }}>
                  Move
                </button>
              </div>
              <p className="helper">The generation date recalculates from the plan&apos;s lead time.</p>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
