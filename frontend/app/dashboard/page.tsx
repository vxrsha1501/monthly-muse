"use client";

/* Dashboard: action-first (Section 3.1) - "What needs my attention this month?" */
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import {
  ArrowRight, CalendarClock, CheckCircle2, FilePlus2, Sparkles, TrendingUp,
} from "lucide-react";
import { http } from "@/lib/api";
import type { Dashboard as DashboardData } from "@/lib/types";
import { CardSkeleton, EmptyState, StatusChip, fmtDate, pct } from "@/components/ui";

export default function DashboardPage() {
  const router = useRouter();
  const { data, isLoading, error } = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => http.get<DashboardData>("/analytics/summary"),
    refetchInterval: 15_000,
  });

  if (isLoading) {
    return (
      <div className="space-y-4">
        <CardSkeleton />
        <div className="grid sm:grid-cols-3 gap-4"><CardSkeleton /><CardSkeleton /><CardSkeleton /></div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="card p-6 text-body text-danger">
        We couldn&apos;t load your dashboard. Check that the API is running.
      </div>
    );
  }

  const stats = data.quick_stats;
  const noPlans = data.next_action?.cta_href === "/plans" && data.upcoming.length === 0;

  return (
    <div className="space-y-5 animate-fade">
      <div>
        <h1 className="text-display">Dashboard</h1>
        <p className="text-body text-muted mt-1">What needs your attention this month.</p>
      </div>

      {/* next action banner */}
      {data.next_action && (
        <div className="card p-5 flex flex-col sm:flex-row sm:items-center gap-4 border-primary-200 bg-primary-50/40">
          <div className="w-10 h-10 rounded-full bg-primary-600 text-white flex items-center justify-center shrink-0">
            {noPlans ? <FilePlus2 className="w-5 h-5" /> : <Sparkles className="w-5 h-5" />}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-h3 text-ink">{data.next_action.title}</p>
            {data.next_action.subtitle && (
              <p className="text-body text-ink-2 mt-0.5">{data.next_action.subtitle}</p>
            )}
          </div>
          {data.next_action.cta_label && (
            <button className="btn-primary shrink-0" onClick={() => router.push(data.next_action!.cta_href ?? "/create")}>
              {data.next_action.cta_label} <ArrowRight className="w-4 h-4" />
            </button>
          )}
        </div>
      )}

      {/* this month card + upcoming timeline */}
      <div className="grid lg:grid-cols-3 gap-4">
        <div className="card p-5 lg:col-span-1">
          <h3 className="text-h3 mb-3">This month</h3>
          {data.this_month ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-h2 font-mono">
                  {fmtDate(data.this_month.post_at, { month: "short", day: "numeric" })}
                </span>
                <StatusChip status={data.this_month.status} />
              </div>
              <p className="text-body text-muted">
                {data.this_month.plan_name ?? "Monthly post"} · generates{" "}
                {fmtDate(data.this_month.generate_at)}
              </p>
              {data.this_month.status === "AWAITING_REVIEW" && (
                <button className="btn-primary w-full" onClick={() => router.push("/review")}>
                  Review 3 candidates
                </button>
              )}
            </div>
          ) : (
            <p className="text-body text-muted">
              No cycle this month. {data.upcoming[0] && `Next: ${fmtDate(data.upcoming[0].post_at)}.`}
            </p>
          )}
        </div>

        <div className="card p-5 lg:col-span-2">
          <h3 className="text-h3 mb-3">Upcoming timeline</h3>
          {data.upcoming.length === 0 ? (
            <p className="text-body text-muted">Nothing scheduled yet.</p>
          ) : (
            <ol className="space-y-3">
              {data.upcoming.map((cycle) => (
                <li key={cycle.id} className="flex items-center gap-3">
                  <CalendarClock className="w-4 h-4 text-muted shrink-0" />
                  <div className="flex-1 min-w-0">
                    <span className="text-body text-ink font-medium">
                      {fmtDate(cycle.post_at, { month: "long", day: "numeric" })}
                    </span>
                    <span className="text-body text-muted"> · {cycle.plan_name ?? "plan"}</span>
                  </div>
                  <span className="text-small text-muted hidden sm:block">
                    generates {fmtDate(cycle.generate_at)}
                  </span>
                  <StatusChip status={cycle.status} />
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>

      {/* quick stats */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="card p-4">
          <p className="text-small text-muted">Messages generated</p>
          <p className="text-h1 font-mono mt-1">{stats.messages_generated}</p>
        </div>
        <div className="card p-4">
          <p className="text-small text-muted">Selection rate</p>
          <p className="text-h1 font-mono mt-1">{pct(stats.selection_rate)}</p>
          <p className="text-small text-muted">
            {stats.selection_rate_ci
              ? `95% CI ${pct(stats.selection_rate_ci[0])}–${pct(stats.selection_rate_ci[1])} · n=${stats.n}`
              : `n=${stats.n}`}
          </p>
        </div>
        <div className="card p-4">
          <p className="text-small text-muted">Average novelty</p>
          <p className="text-h1 font-mono mt-1">{pct(stats.avg_novelty)}</p>
          <p className="text-small text-muted">vs your history</p>
        </div>
        <div className="card p-4">
          <p className="text-small text-muted mb-2 flex items-center gap-1.5">
            <TrendingUp className="w-3.5 h-3.5" /> Tone mix (selected)
          </p>
          {Object.keys(stats.tone_mix).length === 0 ? (
            <p className="text-body text-muted">—</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(stats.tone_mix).map(([tone, count]) => (
                <span key={tone} className="badge bg-primary-50 text-primary-700 capitalize">
                  {tone} · {count}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* recent activity */}
      <div className="card p-5">
        <h3 className="text-h3 mb-3">Recent activity</h3>
        {data.recent_activity.length === 0 ? (
          <p className="text-body text-muted">No actions yet - your first batch will appear here.</p>
        ) : (
          <ul className="space-y-2.5">
            {data.recent_activity.map((event) => (
              <li key={event.id} className="flex items-center gap-3 text-body">
                <CheckCircle2 className="w-4 h-4 text-success shrink-0" />
                <span className="text-ink flex-1">{event.label}</span>
                <span className="text-small text-muted">{fmtDate(event.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {noPlans && (
        <EmptyState
          title="Create your first Monthly Plan"
          subtitle="Set topic, audience and posting day once - then every month a ready-to-approve shortlist appears."
          action={<button className="btn-primary" onClick={() => router.push("/plans")}>Create a plan</button>}
        />
      )}
    </div>
  );
}
