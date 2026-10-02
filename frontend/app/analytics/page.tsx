"use client";

/* Analytics (Section 3.6): Usage / Content / Quality tabs with sample sizes. */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { http } from "@/lib/api";
import type { ContentAnalytics, QualityAnalytics, UsageAnalytics } from "@/lib/types";
import { EmptyState, pct } from "@/components/ui";

const CHART_COLORS = ["#4F46E5", "#0D9488", "#F59E0B", "#DB2777", "#0284C7", "#7C3AED", "#64748B"];

function SampleNote({ n, children }: { n: number; children?: React.ReactNode }) {
  return (
    <p className="text-small text-muted mt-1">
      n = {n}{children ? ` · ${children}` : ""}
      {n < 30 && " · small sample: treat as indicative"}
    </p>
  );
}

export default function AnalyticsPage() {
  const [tab, setTab] = useState<"usage" | "content" | "quality">("usage");

  const usage = useQuery({ queryKey: ["analytics", "usage"], queryFn: () => http.get<UsageAnalytics>("/analytics/usage") });
  const content = useQuery({ queryKey: ["analytics", "content"], queryFn: () => http.get<ContentAnalytics>("/analytics/content") });
  const quality = useQuery({ queryKey: ["analytics", "quality"], queryFn: () => http.get<QualityAnalytics>("/analytics/quality") });

  const toneData = Object.entries(content.data?.tone_distribution ?? {}).map(([name, value]) => ({ name, value }));
  const topicData = Object.entries(content.data?.topic_distribution ?? {}).map(([name, count]) => ({ name, count }));
  const ciData = (quality.data?.selection_by_tone ?? []).map((row) => ({
    ...row, low: Math.round(row.ci[0] * 100), high: Math.round(row.ci[1] * 100),
    band: Math.round((row.ci[1] - row.ci[0]) * 100),
  }));

  return (
    <div className="space-y-5 animate-fade">
      <div>
        <h1 className="text-display">Analytics</h1>
        <p className="text-body text-muted mt-1">Evidence that the AI components work - with sample sizes, always.</p>
      </div>

      <div className="flex gap-1 p-1 bg-white border border-line rounded-input w-fit" role="tablist">
        {(["usage", "content", "quality"] as const).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`px-4 h-9 rounded-[6px] text-body capitalize transition-colors
              ${tab === t ? "bg-primary-600 text-white font-medium" : "text-ink-2 hover:bg-canvas"}`}>
            {t}
          </button>
        ))}
      </div>

      {/* ---------------- usage ---------------- */}
      {tab === "usage" && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {(usage.data?.cards ?? []).map((card) => (
              <div key={card.label} className="card p-4">
                <p className="text-small text-muted">{card.label}</p>
                <p className="text-h1 font-mono mt-1">
                  {typeof card.value === "number" && card.value <= 1 && card.label.includes("rate")
                    ? pct(card.value) : card.value ?? "—"}
                </p>
                {card.detail && <p className="text-small text-muted">{card.detail}</p>}
              </div>
            ))}
          </div>

          <div className="grid lg:grid-cols-2 gap-4">
            <div className="card p-5">
              <h3 className="text-h3">Generated per week</h3>
              <SampleNote n={usage.data?.weekly?.length ?? 0} />
              <div className="h-56 mt-3">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={usage.data?.weekly ?? []}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
                    <XAxis dataKey="week" tick={{ fontSize: 11, fill: "#64748B" }} />
                    <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "#64748B" }} width={30} />
                    <Tooltip />
                    <Line type="monotone" dataKey="generated" stroke="#4F46E5" strokeWidth={2} dot={{ r: 3 }} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="card p-5">
              <h3 className="text-h3">Workflow rates</h3>
              <div className="mt-4 space-y-4">
                {[
                  { label: "Regeneration rate", value: usage.data?.regeneration_rate, hint: "Target: decreasing, < 30% by month 3" },
                  { label: "Edit rate", value: usage.data?.edit_rate, hint: "Lower means candidates match your voice" },
                ].map((row) => (
                  <div key={row.label}>
                    <div className="flex justify-between text-body">
                      <span className="text-ink-2">{row.label}</span>
                      <span className="font-mono">{pct(row.value)}</span>
                    </div>
                    <div className="h-2 rounded-full bg-slate-100 mt-1.5 overflow-hidden">
                      <div className="h-full bg-primary-600 rounded-full transition-all"
                        style={{ width: `${Math.round((row.value ?? 0) * 100)}%` }} />
                    </div>
                    <p className="text-small text-muted mt-1">{row.hint}</p>
                  </div>
                ))}
                <div className="pt-3 border-t border-line flex justify-between text-body">
                  <span className="text-ink-2">Average time to approve</span>
                  <span className="font-mono">{usage.data?.avg_time_to_approve_hours != null ? `${usage.data.avg_time_to_approve_hours}h` : "—"}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ---------------- content ---------------- */}
      {tab === "content" && (
        <div className="space-y-4">
          <div className="grid lg:grid-cols-3 gap-4">
            <div className="card p-5">
              <h3 className="text-h3">Tone mix</h3>
              <SampleNote n={content.data?.entropy_n ?? 0}>of selected messages</SampleNote>
              {toneData.length === 0 ? (
                <p className="text-body text-muted mt-4">Select at least one message to see tone preferences.</p>
              ) : (
                <div className="h-52 mt-2">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie data={toneData} dataKey="value" nameKey="name" innerRadius={45} outerRadius={75} paddingAngle={2}>
                        {toneData.map((_, i) => <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />)}
                      </Pie>
                      <Tooltip />
                      <Legend wrapperStyle={{ fontSize: 12 }} />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>

            <div className="card p-5 lg:col-span-2">
              <h3 className="text-h3">Topics</h3>
              <div className="h-52 mt-2">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={topicData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
                    <XAxis dataKey="name" tick={{ fontSize: 10, fill: "#64748B" }} interval={0} angle={-18} textAnchor="end" height={50} />
                    <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "#64748B" }} width={25} />
                    <Tooltip />
                    <Bar dataKey="count" fill="#0D9488" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>

          <div className="grid lg:grid-cols-2 gap-4">
            <div className="card p-5">
              <h3 className="text-h3">Novelty trend</h3>
              <p className="text-body text-muted">1 − max cosine to your history, per month of selection.</p>
              <SampleNote n={content.data?.novelty_trend?.length ?? 0} />
              <div className="h-52 mt-2">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={content.data?.novelty_trend ?? []}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
                    <XAxis dataKey="month" tick={{ fontSize: 11, fill: "#64748B" }} />
                    <YAxis domain={[0, 1]} tick={{ fontSize: 11, fill: "#64748B" }} width={30} />
                    <Tooltip formatter={(v) => pct(v == null ? undefined : Number(v))} />
                    <Line type="monotone" dataKey="avg_novelty" stroke="#DB2777" strokeWidth={2} dot={{ r: 3 }} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="card p-5">
              <h3 className="text-h3">Diversity & repetition</h3>
              <div className="mt-3 space-y-3 text-body">
                <div className="flex justify-between">
                  <span className="text-ink-2">Normalised entropy (tone diversity)</span>
                  <span className="font-mono">{content.data?.entropy ?? "—"}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-ink-2">Average novelty</span>
                  <span className="font-mono">{pct(content.data?.avg_novelty)}</span>
                </div>
              </div>
              <div className="mt-4 pt-3 border-t border-line">
                <p className="text-small font-semibold text-ink mb-2">Repetition alerts (cos ≥ 0.85)</p>
                {(content.data?.repetition_alerts ?? []).length === 0 ? (
                  <p className="text-body text-muted">No near-repeats in your last six posts. 👍</p>
                ) : (
                  <ul className="space-y-1.5">
                    {(content.data?.repetition_alerts ?? []).map((alert, i) => (
                      <li key={i} className="text-body text-amber-700 bg-amber-50 rounded-input px-3 py-2">
                        Two recent posts are {Math.round(alert.cosine * 100)}% similar - you sound similar every month.
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </div>

          {(content.data?.month_tone_heatmap?.length ?? 0) > 0 && (
            <div className="card p-5 overflow-x-auto">
              <h3 className="text-h3">Month × tone heatmap</h3>
              <table className="mt-3 text-body min-w-full">
                <thead>
                  <tr className="text-small text-muted">
                    <th className="text-left py-2 pr-4">Month</th>
                    <th className="text-left py-2">Tone</th>
                    <th className="text-left py-2 pl-4">Count</th>
                  </tr>
                </thead>
                <tbody>
                  {(content.data?.month_tone_heatmap ?? []).map((row, i) => (
                    <tr key={i} className="border-t border-line">
                      <td className="py-2 pr-4 font-mono">{row.month}</td>
                      <td className="py-2 capitalize">{row.tone}</td>
                      <td className="py-2 pl-4">
                        <span className="inline-block h-4 rounded bg-primary-600"
                          style={{ width: Math.min(120, row.count * 24), opacity: 0.35 + row.count * 0.2 }} />
                        <span className="ml-2 font-mono text-small">{row.count}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ---------------- quality ---------------- */}
      {tab === "quality" && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {[
              { label: "Avg selected score", value: quality.data?.avg_selected_score?.toFixed(3) ?? "—" },
              { label: "Top-1 agreement", value: pct(quality.data?.top1_agreement) },
              { label: "MRR", value: quality.data?.mrr?.toFixed(3) ?? "—" },
              { label: "Score↔selection r", value: quality.data?.score_selection_correlation?.toFixed(3) ?? "—" },
            ].map((card) => (
              <div key={card.label} className="card p-4">
                <p className="text-small text-muted">{card.label}</p>
                <p className="text-h1 font-mono mt-1">{card.value}</p>
              </div>
            ))}
          </div>

          <div className="grid lg:grid-cols-2 gap-4">
            <div className="card p-5">
              <h3 className="text-h3">Selection rate by tone</h3>
              <SampleNote n={quality.data?.n ?? 0} />
              <div className="h-56 mt-3">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={ciData} layout="vertical" margin={{ left: 8 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
                    <XAxis type="number" domain={[0, 100]} tick={{ fontSize: 11, fill: "#64748B" }} unit="%" />
                    <YAxis type="category" dataKey="tone" tick={{ fontSize: 11, fill: "#64748B" }} width={80} />
                    <Tooltip formatter={(v, name) => [`${Number(v)}%`, String(name)]} />
                    <Bar dataKey="rate" name="selected" fill="#4F46E5" radius={[0, 4, 4, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <ul className="mt-2 space-y-1">
                {(quality.data?.selection_by_tone ?? []).map((row) => (
                  <li key={row.tone} className="text-small text-muted flex justify-between">
                    <span className="capitalize">{row.tone}</span>
                    <span className="font-mono">
                      {Math.round(row.rate * 100)}% (CI {Math.round(row.ci[0] * 100)}–{Math.round(row.ci[1] * 100)}%), n={row.n}
                    </span>
                  </li>
                ))}
              </ul>
            </div>

            <div className="card p-5">
              <h3 className="text-h3">System performance</h3>
              <dl className="mt-3 space-y-2.5 text-body">
                <div className="flex justify-between"><dt className="text-muted">Latency p50</dt>
                  <dd className="font-mono">{quality.data?.latency_p50 != null ? `${quality.data.latency_p50}ms` : "—"}</dd></div>
                <div className="flex justify-between"><dt className="text-muted">Latency p95</dt>
                  <dd className="font-mono">{quality.data?.latency_p95 != null ? `${quality.data.latency_p95}ms` : "—"}</dd></div>
                <div className="flex justify-between"><dt className="text-muted">LLM failure rate</dt>
                  <dd className="font-mono">{pct(quality.data?.llm_failure_rate)}</dd></div>
                <div className="flex justify-between"><dt className="text-muted">Template fallback rate</dt>
                  <dd className="font-mono">{pct(quality.data?.fallback_rate)}</dd></div>
              </dl>

              <div className="mt-4 pt-3 border-t border-line">
                <p className="text-small font-semibold text-ink mb-1">Tone × selection test (chi-square)</p>
                {quality.data?.tone_selection_test?.insufficient ? (
                  <p className="text-body text-muted">
                    Not enough data yet for a significance claim - counts need expected ≥ 5 per cell.
                  </p>
                ) : (
                  <p className="text-body text-ink-2">
                    χ² = {quality.data?.tone_selection_test.chi2?.toFixed(2)} ·{" "}
                    p = {quality.data?.tone_selection_test.p_value?.toFixed(3)} ·{" "}
                    df = {quality.data?.tone_selection_test.dof}{" "}
                    {((quality.data?.tone_selection_test.p_value ?? 1) < 0.05)
                      ? "→ your choices depend significantly on tone (p < 0.05)."
                      : "→ no significant dependence detected yet."}
                  </p>
                )}
              </div>
            </div>
          </div>

          {(quality.data?.n ?? 0) === 0 && (
            <EmptyState title="No scored candidates yet"
              subtitle="Generate a batch and review it - metrics appear after your first decisions." />
          )}
        </div>
      )}
    </div>
  );
}
