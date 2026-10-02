"use client";

/* Message History (Section 3.5): text + semantic search, filters, detail drawer. */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, RotateCw, Search, Trash2, X } from "lucide-react";
import { http, qs } from "@/lib/api";
import type { HistoryItem, MessageDetail } from "@/lib/types";
import { EmptyState, Modal, ScoreRing, StatusChip, fmtDate, pct, useToast } from "@/components/ui";

const TONE_FILTERS = ["warm", "playful", "professional", "inspirational", "witty", "grateful", "urgent", "formal"];

export default function HistoryPage() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [q, setQ] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [semantic, setSemantic] = useState(false);
  const [tone, setTone] = useState("");
  const [month, setMonth] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [detailId, setDetailId] = useState<string | null>(null);

  const query = qs({ q: submitted, tone, month, status, semantic, page, page_size: 12 });
  const { data, isLoading } = useQuery({
    queryKey: ["history", query],
    queryFn: () => http.get<{ items: HistoryItem[]; total: number }>(`/messages${query}`),
  });

  const { data: detail } = useQuery({
    queryKey: ["message", detailId],
    queryFn: () => http.get<MessageDetail>(`/messages/${detailId}`),
    enabled: !!detailId,
  });

  const remove = useMutation({
    mutationFn: (id: string) => http.del(`/messages/${id}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["history"] });
      toast("Message archived.");
    },
  });

  const reuse = useMutation({
    mutationFn: (id: string) => http.post<Record<string, unknown>>(`/messages/${id}/reuse`),
    onSuccess: (inputs) => {
      try {
        localStorage.setItem("mm_draft_post", JSON.stringify({
          month: (inputs.month as string) ?? undefined,
          topic: (inputs.topic as string) ?? "",
          topic_id: (inputs.topic_id as string) ?? null,
          occasion: (inputs.occasion as string) ?? null,
          audience_id: (inputs.audience_id as string) ?? null,
          tones: (inputs.tones as string[]) ?? ["warm"],
          purpose: (inputs.purpose as string) ?? "inform",
          platform: (inputs.platform as string) ?? "instagram",
          length: (inputs.length as string) ?? "medium",
          language: "en",
          keywords: (inputs.keywords as string[]) ?? [],
          cta: (inputs.cta as string) ?? "",
          additional_instructions: "",
        }));
      } catch { /* ignore */ }
      window.location.href = "/create";
    },
    onError: (e: Error) => toast(e.message, "error"),
  });

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / 12));

  return (
    <div className="space-y-5 animate-fade">
      <div>
        <h1 className="text-display">History</h1>
        <p className="text-body text-muted mt-1">
          Full-text search, or toggle semantic search to find posts by meaning (cosine ranking).
        </p>
      </div>

      {/* search + filters */}
      <div className="card p-4 space-y-3">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); setPage(1); setSubmitted(q); }}>
          <div className="relative flex-1">
            <Search className="w-4 h-4 absolute left-3 top-3 text-muted" />
            <input className="input !pl-9" value={q} onChange={(e) => setQ(e.target.value)}
              placeholder={semantic ? "gratitude post for volunteers (by meaning)" : "search your messages…"}
              aria-label="Search messages" />
          </div>
          <button type="button"
            className={`btn-secondary ${semantic ? "!bg-primary-50 !border-primary-200 !text-primary-700" : ""}`}
            onClick={() => setSemantic((v) => !v)} aria-pressed={semantic} title="Search by embedding similarity">
            Semantic
          </button>
          <button className="btn-primary" type="submit">Search</button>
        </form>

        <div className="flex flex-wrap gap-2">
          <select className="input !w-auto !h-8 !text-small" value={tone} onChange={(e) => setTone(e.target.value)} aria-label="Filter by tone">
            <option value="">All tones</option>
            {TONE_FILTERS.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <input type="month" className="input !w-auto !h-8 !text-small" value={month}
            onChange={(e) => setMonth(e.target.value)} aria-label="Filter by month" />
          <select className="input !w-auto !h-8 !text-small" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status">
            <option value="">Any status</option>
            <option value="SELECTED">Selected</option>
            <option value="SHOWN">Shown</option>
            <option value="EDITED">Edited</option>
            <option value="REJECTED">Rejected</option>
          </select>
          {(submitted || tone || month || status) && (
            <button className="btn-ghost btn-sm" onClick={() => { setQ(""); setSubmitted(""); setTone(""); setMonth(""); setStatus(""); }}>
              <X className="w-4 h-4" /> Clear
            </button>
          )}
        </div>
      </div>

      {/* results */}
      {isLoading ? (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {[0, 1, 2].map((i) => <div key={i} className="card p-5 animate-pulse h-40 bg-slate-50" />)}
        </div>
      ) : items.length === 0 ? (
        <EmptyState title="No messages yet"
          subtitle="Generate your first monthly post to start building your content memory."
          action={<a className="btn-primary" href="/create">Generate a post</a>} />
      ) : (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {items.map((item) => (
            <article key={item.id} className="card p-4 flex flex-col hover:shadow-md transition-shadow cursor-pointer"
              onClick={() => setDetailId(item.id)}>
              <div className="flex items-center justify-between gap-2 mb-2">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="badge bg-primary-50 text-primary-700 capitalize">{item.tone ?? "tone?"}</span>
                  <StatusChip status={item.status} />
                </div>
                <span className="text-small text-muted font-mono shrink-0">
                  {item.score !== null ? item.score.toFixed(2) : "—"}
                </span>
              </div>
              <p className="text-body text-ink line-clamp-4 flex-1">{item.text}</p>
              <div className="mt-3 pt-3 border-t border-line flex items-center justify-between text-small text-muted">
                <span>{item.month ?? fmtDate(item.created_at)}</span>
                <span className="flex items-center gap-2">
                  {item.similarity !== null && <span className="font-mono">{pct(item.similarity)}</span>}
                  {item.novelty !== null && <span>novel {pct(item.novelty)}</span>}
                </span>
              </div>
            </article>
          ))}
        </div>
      )}

      {pages > 1 && (
        <div className="flex items-center justify-center gap-3">
          <button className="btn-secondary btn-sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Previous</button>
          <span className="text-small text-muted">Page {page} of {pages} · {total} messages</span>
          <button className="btn-secondary btn-sm" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>Next</button>
        </div>
      )}

      {/* detail drawer */}
      <Modal open={!!detailId} onClose={() => setDetailId(null)} title="Message detail">
        {detail ? (
          <div className="space-y-4">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <span className="badge bg-primary-50 text-primary-700 capitalize">{detail.message.tone}</span>
                  <StatusChip status={detail.message.status} />
                </div>
                <p className="text-small text-muted">{detail.message.style_label} · {fmtDate(detail.message.created_at)}</p>
              </div>
              {detail.message.score !== null && <ScoreRing score={detail.message.score} size={52} />}
            </div>

            <p className="text-message text-ink whitespace-pre-wrap">{detail.message.text}</p>

            <div className="rounded-card border border-line p-3">
              <p className="text-small font-semibold mb-2">Original inputs</p>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-body">
                {Object.entries(detail.inputs).filter(([k]) => !k.startsWith("_")).slice(0, 8).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-2">
                    <dt className="text-muted capitalize">{k.replace(/_/g, " ")}</dt>
                    <dd className="font-medium text-right truncate max-w-36">
                      {Array.isArray(v) ? v.join(", ") : String(v ?? "—")}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>

            <div className="rounded-card border border-line p-3">
              <p className="text-small font-semibold mb-2">Full batch ({detail.batch.length})</p>
              <ul className="space-y-1.5">
                {detail.batch.map((m) => (
                  <li key={m.id} className="flex items-center gap-2 text-body">
                    <span className="w-5 text-small text-muted font-mono">{m.rank ?? "·"}</span>
                    <span className="truncate flex-1">{m.text}</span>
                    <span className="font-mono text-small text-muted">{m.score?.toFixed(2) ?? ""}</span>
                    <StatusChip status={m.status} />
                  </li>
                ))}
              </ul>
            </div>

            {detail.feedback.length > 0 && (
              <div className="rounded-card border border-line p-3">
                <p className="text-small font-semibold mb-2">Feedback events</p>
                <ul className="space-y-1 text-body">
                  {detail.feedback.map((f) => (
                    <li key={f.id} className="flex justify-between gap-2">
                      <span className="capitalize">{f.event_type.replace(/_/g, " ")}{f.reason ? ` · ${f.reason}` : ""}</span>
                      <span className="font-mono text-small text-muted">{f.reward > 0 ? "+" : ""}{f.reward}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className="flex flex-wrap gap-2 pt-2 border-t border-line">
              <button className="btn-primary btn-sm" onClick={() => void reuse.mutate(detail.message.id)}>
                <RotateCw className="w-4 h-4" /> Generate similar
              </button>
              <button className="btn-secondary btn-sm"
                onClick={() => { void navigator.clipboard?.writeText(detail.message.text); toast("Copied."); }}>
                <Copy className="w-4 h-4" /> Copy
              </button>
              <button className="btn-danger btn-sm"
                onClick={() => { void remove.mutate(detail.message.id); setDetailId(null); }}>
                <Trash2 className="w-4 h-4" /> Delete
              </button>
            </div>
          </div>
        ) : (
          <div className="animate-pulse h-40 bg-slate-50 rounded-input" />
        )}
      </Modal>
    </div>
  );
}
