"use client";

/* Review & compare screen (Section 3.3): three ranked candidates with score
   breakdown, similarity meter, confidence, compare mode, embedding map, actions. */
import { useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BarChart3, Check, CheckCircle2, ChevronDown, Copy, Map, Pencil,
  RefreshCw, Send, ThumbsDown, ThumbsUp, X,
} from "lucide-react";
import {
  CartesianGrid, Cell, Legend, PolarAngleAxis, PolarGrid,
  PolarRadiusAxis, Radar, RadarChart, ResponsiveContainer, Scatter, ScatterChart,
  Tooltip, XAxis, YAxis, ZAxis,
} from "recharts";
import { http } from "@/lib/api";
import type { Candidate, Cycle, Stage } from "@/lib/types";
import {
  ConfidenceChip, FeatureBar, Modal, ScoreRing, SimilarityBadge,
  useToast,
} from "@/components/ui";
import { REJECT_REASONS } from "@/lib/types";

const UI_STAGES = [
  { key: "understand", label: "Understanding your context" },
  { key: "history", label: "Reading your previous posts" },
  { key: "generate", label: "Writing candidates" },
  { key: "quality", label: "Checking quality and similarity" },
  { key: "rank", label: "Ranking and selecting the best three" },
];

function uniqueWords(text: string, others: string[]): string[] {
  const words = text.split(/\s+/);
  const other = others.join(" ").toLowerCase();
  const seen = new Set<string>();
  return words.filter((w) => {
    const clean = w.toLowerCase().replace(/[^a-z0-9']/g, "");
    if (clean.length < 4 || seen.has(clean)) return false;
    seen.add(clean);
    return !other.includes(clean);
  });
}

export default function ReviewPage() {
  const { requestId } = useParams<{ requestId: string }>();
  const router = useRouter();
  const toast = useToast();
  const queryClient = useQueryClient();

  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftText, setDraftText] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [compare, setCompare] = useState(false);
  const [showMap, setShowMap] = useState(false);
  const [rejectTarget, setRejectTarget] = useState<Candidate | null>(null);
  const [rejectReason, setRejectReason] = useState<string>("too_similar");
  const [regenOpen, setRegenOpen] = useState(false);
  const [regenInstruction, setRegenInstruction] = useState("");

  const { data: stage, isLoading } = useQuery({
    queryKey: ["request", requestId],
    queryFn: () => http.get<Stage>(`/generation-requests/${requestId}`),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "DONE" || status === "FAILED" ? false : 1200;
    },
  });

  const { data: cycles } = useQuery({
    queryKey: ["cycles", "review"],
    queryFn: () => http.get<Cycle[]>("/cycles?status=AWAITING_REVIEW"),
    enabled: stage?.status === "DONE",
  });

  const cycle = useMemo(
    () => (cycles ?? []).find((c) => c.latest_request_id === requestId),
    [cycles, requestId],
  );

  const candidates = useMemo(
    () => (stage?.candidates ?? []).filter((c) => c.rank && c.status !== "FILTERED")
      .sort((a, b) => (a.rank ?? 9) - (b.rank ?? 9)),
    [stage],
  );
  const filtered = useMemo(
    () => (stage?.candidates ?? []).filter((c) => c.status === "FILTERED"),
    [stage],
  );
  /* ---------------- actions ---------------- */
  const act = useMutation({
    mutationFn: ({ path, body, method = "POST" }: { path: string; body?: unknown; method?: string }) =>
      method === "PATCH" ? http.patch(path, body) : http.post(path, body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["request", requestId] }),
    onError: (err: Error) => toast(err.message, "error"),
  });

  const selectCandidate = async (c: Candidate) => {
    await act.mutateAsync({ path: `/messages/${c.id}/select` }).catch(() => null);
    if (cycle) {
      try {
        await http.post(`/cycles/${cycle.id}/approve`, { message_id: c.id });
        toast("Selected and scheduled for posting - next month will learn from this.");
      } catch {
        toast("Selected - approve it from the calendar to schedule.", "info");
      }
    } else {
      toast("Selected as your final message.");
    }
    void queryClient.invalidateQueries({ queryKey: ["request", requestId] });
    void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  };

  const saveEdit = async (c: Candidate) => {
    await act.mutateAsync({ path: `/messages/${c.id}`, body: { final_text: draftText }, method: "PATCH" }).catch(() => null);
    setEditingId(null);
    toast("Edit saved - the embedding was recomputed for the new text.");
  };

  const feedback = async (c: Candidate, type: "like" | "dislike" | "copy") => {
    if (type === "copy") await navigator.clipboard?.writeText(c.final_text ?? c.text).catch(() => null);
    await act.mutateAsync({ path: `/messages/${c.id}/feedback`, body: { event_type: type } }).catch(() => null);
    toast(type === "copy" ? "Copied to clipboard." : type === "like" ? "Noted - you liked this style." : "Noted - less of this style.");
  };

  const submitReject = async () => {
    if (!rejectTarget) return;
    await act.mutateAsync({
      path: `/messages/${rejectTarget.id}/feedback`,
      body: { event_type: "reject", reason: rejectReason },
    }).catch(() => null);
    toast("Rejection stored - the matching weight was nudged.");
    setRejectTarget(null);
  };

  const regenerate = async () => {
    const path = regenInstruction
      ? `/generation-requests/${requestId}/regenerate?instruction=${encodeURIComponent(regenInstruction)}`
      : `/generation-requests/${requestId}/regenerate`;
    try {
      const res = await http.post<{ request_id: string }>(path);
      router.push(`/review/${res.request_id}`);
    } catch (err) {
      toast((err as Error).message, "error");
    }
  };

  const radarData = useMemo(() => {
    const keys = ["relevance", "tone", "personalization", "novelty", "length", "history"] as const;
    return keys.map((k) => ({
      factor: k,
      ...Object.fromEntries(candidates.map((c, i) => [`c${i}`, Math.round((c.features[k] ?? 0) * 100)])),
    }));
  }, [candidates]);

  const mapData = stage?.embedding_map;

  /* ---------------- running state ---------------- */
  if (isLoading || !stage) {
    return <div className="card p-6"><div className="animate-pulse h-40 bg-slate-100 rounded-input" /></div>;
  }

  if (stage.status === "FAILED") {
    return (
      <div className="card p-6 border-danger/30">
        <h1 className="text-h2 mb-2">Generation failed</h1>
        <p className="text-body text-ink-2 mb-4">{stage.error ?? "The pipeline hit an error."}</p>
        <button className="btn-primary" onClick={() => void regenerate()}>Try again</button>
      </div>
    );
  }

  if (stage.status !== "DONE") {
    const activeIdx = UI_STAGES.findIndex((s) => s.key === stage.ui_stage);
    return (
      <div className="max-w-xl mx-auto py-8 animate-fade">
        <div className="card p-6">
          <h1 className="text-h2 mb-1">Preparing your post</h1>
          <p className="text-body text-muted mb-6">Real stages from the pipeline - no fake spinner.</p>
          <ol className="space-y-3" aria-live="polite">
            {UI_STAGES.map((s, i) => {
              const done = activeIdx > i || stage.ui_stage === "done";
              const active = activeIdx === i;
              return (
                <li key={s.key} className="flex items-center gap-3">
                  <span className={`w-6 h-6 rounded-full flex items-center justify-center shrink-0
                    ${done ? "bg-success text-white" : active ? "bg-primary-600 text-white" : "bg-slate-100 text-slate-400"}`}>
                    {done ? <Check className="w-3.5 h-3.5" /> : <span className="w-2 h-2 rounded-full bg-current" />}
                  </span>
                  <span className={`text-body ${active ? "text-ink font-medium" : done ? "text-ink-2" : "text-muted"}`}>
                    {s.label}
                  </span>
                  {active && <span className="ml-auto text-small text-primary-700 animate-pulseDot">working…</span>}
                </li>
              );
            })}
          </ol>
        </div>
      </div>
    );
  }

  /* ---------------- DONE ---------------- */
  return (
    <div className="space-y-5 animate-fade">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-display">Review your candidates</h1>
          <p className="text-body text-muted mt-1">
            {stage.provider === "template"
              ? "Template-generated (LLM unavailable) - same scoring and novelty checks."
              : `Generated by ${stage.provider ?? "the pipeline"}`}
            {stage.latency_ms ? ` · ${stage.latency_ms}ms` : ""}
            {stage.retrieval.n_history !== undefined ? ` · ${stage.retrieval.n_history} past posts used` : ""}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className={`btn-secondary ${compare ? "!bg-primary-50 !border-primary-200" : ""}`}
            onClick={() => setCompare((v) => !v)} aria-pressed={compare}>
            <BarChart3 className="w-4 h-4" /> Compare
          </button>
          <button className={`btn-secondary ${showMap ? "!bg-primary-50 !border-primary-200" : ""}`}
            onClick={() => setShowMap((v) => !v)} aria-pressed={showMap}>
            <Map className="w-4 h-4" /> Embedding map
          </button>
          <button className="btn-secondary" onClick={() => setRegenOpen(true)}>
            <RefreshCw className="w-4 h-4" /> Regenerate all
          </button>
          <button className="btn-ghost" onClick={() => router.push("/create")}>Back to inputs</button>
        </div>
      </div>

      {/* compare mode: side by side + radar */}
      {compare && candidates.length >= 2 && (
        <div className="card p-5 animate-fade">
          <div className="grid lg:grid-cols-2 gap-6">
            <div className="space-y-3">
              <h3 className="text-h3">Side-by-side</h3>
              <div className={`grid gap-3 ${candidates.length === 3 ? "grid-cols-1 sm:grid-cols-3" : "grid-cols-1 sm:grid-cols-2"}`}>
                {candidates.map((c) => {
                  const others = candidates.filter((o) => o.id !== c.id).map((o) => o.text);
                  const highlights = uniqueWords(c.text, others);
                  return (
                    <div key={c.id} className="rounded-card border border-line p-3">
                      <p className="text-small font-medium text-primary-700 mb-1.5">
                        #{c.rank} {c.style_label}
                      </p>
                      <p className="text-body text-ink leading-6">
                        {c.text.split(/\s+/).map((w, i) => {
                          const clean = w.toLowerCase().replace(/[^a-z0-9']/g, "");
                          const isNew = highlights.includes(w) && clean.length >= 4;
                          return (
                            <span key={i} className={isNew ? "bg-amber-100 rounded px-0.5" : ""}>{w} </span>
                          );
                        })}
                      </p>
                    </div>
                  );
                })}
              </div>
              <p className="text-small text-muted">Highlighted words appear only in that candidate.</p>
            </div>
            <div>
              <h3 className="text-h3 mb-3">Score factors</h3>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <RadarChart data={radarData} outerRadius="70%">
                    <PolarGrid stroke="#E2E8F0" />
                    <PolarAngleAxis dataKey="factor" tick={{ fontSize: 11, fill: "#475569" }} />
                    <PolarRadiusAxis domain={[0, 100]} tick={false} axisLine={false} />
                    {candidates.map((c, i) => (
                      <Radar key={c.id} name={`#${c.rank} ${c.style_label?.split(" - ")[0] ?? ""}`}
                        dataKey={`c${i}`}
                        stroke={["#4F46E5", "#0D9488", "#F59E0B"][i % 3]}
                        fill={["#4F46E5", "#0D9488", "#F59E0B"][i % 3]} fillOpacity={0.12} />
                    ))}
                    <Legend />
                  </RadarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* embedding map */}
      {showMap && mapData && (
        <div className="card p-5 animate-fade">
          <h3 className="text-h3 mb-1">Embedding map (PCA)</h3>
          <p className="text-body text-muted mb-3">
            Candidates far from your past posts (grey) are novel; the star is the intent.
          </p>
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ top: 10, right: 10, bottom: 10, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
                <XAxis type="number" dataKey="x" name="" tick={false} stroke="#94A3B8" />
                <YAxis type="number" dataKey="y" name="" tick={false} stroke="#94A3B8" />
                <ZAxis range={[60, 60]} />
                <Tooltip cursor={{ strokeDasharray: "3 3" }} />
                <Scatter name="Your past posts" data={mapData.history} fill="#94A3B8" />
                <Scatter name="Intent" data={mapData.intent ? [mapData.intent] : []} fill="#F59E0B" shape="star" />
                <Scatter name="Candidates" data={mapData.candidates} fill="#4F46E5">
                  {mapData.candidates.map((_, i) => <Cell key={i} fill={["#4F46E5", "#0D9488", "#7C3AED"][i % 3]} />)}
                </Scatter>
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* candidate cards */}
      <div className={`grid gap-4 ${compare ? "grid-cols-1" : "lg:grid-cols-3"}`}>
        {candidates.map((c) => (
          <article key={c.id}
            className={`card p-5 flex flex-col transition-all duration-150 hover:shadow-md
              ${c.selected ? "ring-2 ring-primary-600 border-primary-600" : ""}`}>
            <div className="flex items-start justify-between gap-3 mb-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="w-6 h-6 rounded-full bg-primary-600 text-white text-small font-semibold flex items-center justify-center shrink-0">
                    {c.rank}
                  </span>
                  <span className="text-small font-medium text-ink truncate">{c.style_label}</span>
                </div>
                <div className="flex flex-wrap items-center gap-1.5 mt-2">
                  <ConfidenceChip level={c.confidence} />
                  {c.is_fallback && <span className="badge bg-slate-100 text-slate-600">Template-generated</span>}
                  {c.explain.arm?.[0] && c.rank === 3 && (
                    <span className="badge bg-violet-50 text-violet-700">Exploring a new style</span>
                  )}
                </div>
              </div>
              <ScoreRing score={c.score ?? 0} />
            </div>

            {/* message text */}
            {editingId === c.id ? (
              <div className="space-y-2">
                <textarea className="input h-auto py-2 text-message" rows={6} value={draftText}
                  onChange={(e) => setDraftText(e.target.value)} aria-label="Edit candidate text" />
                <div className="flex gap-2">
                  <button className="btn-primary btn-sm" onClick={() => void saveEdit(c)}>Save edit</button>
                  <button className="btn-ghost btn-sm" onClick={() => setEditingId(null)}>Cancel</button>
                </div>
              </div>
            ) : (
              <p className="text-message text-ink flex-1 whitespace-pre-wrap">{c.final_text ?? c.text}</p>
            )}

            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-3 text-small text-muted font-mono">
              <span>{c.word_count} words</span>
              <span>{c.char_count} chars</span>
              {c.nearest_past.cosine !== null && (
                <SimilarityBadge band={c.nearest_past.band} cosine={c.nearest_past.cosine} />
              )}
            </div>

            {/* why this ranked */}
            <div className="mt-3 rounded-input bg-canvas border border-line p-3">
              <p className="text-small font-semibold text-ink mb-1">Why this ranked #{c.rank}</p>
              <ul className="space-y-1">
                {(c.explain.reasons ?? []).map((r) => (
                  <li key={r} className="text-small text-ink-2">· {r}</li>
                ))}
                {c.nearest_past.preview && (
                  <li className="text-small text-muted pt-1 border-t border-line">
                    Nearest past post ({c.nearest_past.month}): “{c.nearest_past.preview}…”
                  </li>
                )}
              </ul>
            </div>

            {/* score breakdown */}
            <button className="flex items-center justify-between mt-3 text-small text-ink-2 hover:text-ink"
              onClick={() => setExpanded(expanded === c.id ? null : c.id)}
              aria-expanded={expanded === c.id}>
              <span>Score breakdown</span>
              <ChevronDown className={`w-4 h-4 transition-transform ${expanded === c.id ? "rotate-180" : ""}`} />
            </button>
            {expanded === c.id && (
              <div className="mt-2 space-y-2 animate-fade">
                {Object.entries(c.features).map(([name, value]) => (
                  <FeatureBar key={name} label={name} value={value} />
                ))}
              </div>
            )}

            {/* actions */}
            <div className="mt-4 pt-4 border-t border-line flex flex-wrap gap-2">
              {c.selected ? (
                <span className="btn btn-primary !bg-success hover:!bg-success w-full">
                  <CheckCircle2 className="w-4 h-4" /> Selected
                </span>
              ) : (
                <button className="btn-primary flex-1" onClick={() => void selectCandidate(c)}>
                  <Send className="w-4 h-4" /> Select as final
                </button>
              )}
              <button className="btn-secondary btn-sm" title="Edit"
                onClick={() => { setEditingId(c.id); setDraftText(c.final_text ?? c.text); }}>
                <Pencil className="w-4 h-4" />
              </button>
              <button className="btn-secondary btn-sm" title="Copy"
                onClick={() => void feedback(c, "copy")}>
                <Copy className="w-4 h-4" />
              </button>
              <button className="btn-secondary btn-sm" title="Like this style"
                onClick={() => void feedback(c, "like")}>
                <ThumbsUp className="w-4 h-4" />
              </button>
              <button className="btn-secondary btn-sm" title="Dislike"
                onClick={() => void feedback(c, "dislike")}>
                <ThumbsDown className="w-4 h-4" />
              </button>
              <button className="btn-danger btn-sm" title="Reject with a reason"
                onClick={() => setRejectTarget(c)}>
                <X className="w-4 h-4" />
              </button>
            </div>
          </article>
        ))}
      </div>

      {/* filtered candidates (transparency) */}
      {filtered.length > 0 && (
        <details className="card p-4">
          <summary className="cursor-pointer text-body font-medium">
            {filtered.length} candidate{filtered.length > 1 ? "s" : ""} filtered by quality checks
          </summary>
          <ul className="mt-3 space-y-2">
            {filtered.map((c) => (
              <li key={c.id} className="text-body text-muted flex gap-3">
                <span className="badge bg-slate-100 text-slate-500 shrink-0">
                  {(c.filter_reason ?? "filtered").replace(/_/g, " ")}
                </span>
                <span className="line-clamp-1">{c.text}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {/* what the AI used */}
      <div className="card p-5">
        <h3 className="text-h3 mb-2">What the AI used</h3>
        <div className="grid sm:grid-cols-3 gap-4 text-body text-ink-2">
          <div>
            <p className="text-small font-semibold text-ink mb-1">Style arms (Thompson sampling)</p>
            {stage.arms.length ? stage.arms.map((a, i) => (
              <p key={i} className="capitalize">{i + 1}. {a[0]?.replace("_", " ")} · {a[1]?.replace("-", " ")}{i === 2 ? " (explore)" : ""}</p>
            )) : <p>—</p>}
          </div>
          <div>
            <p className="text-small font-semibold text-ink mb-1">Retrieved</p>
            <p>{stage.retrieval.n_history ?? 0} past posts in the history matrix</p>
            <p>{stage.retrieval.exemplars?.length ?? 0} style exemplars · {stage.retrieval.recent?.length ?? 0} avoided repeats</p>
          </div>
          <div>
            <p className="text-small font-semibold text-ink mb-1">Scoring</p>
            <p>s = F·w with your learned weights, MMR λ=0.7, Monte-Carlo confidence (200 draws)</p>
          </div>
        </div>
      </div>

      {/* reject dialog */}
      <Modal open={!!rejectTarget} onClose={() => setRejectTarget(null)} title="Why are you rejecting this?"
        footer={
          <>
            <button className="btn-ghost" onClick={() => setRejectTarget(null)}>Cancel</button>
            <button className="btn-primary" onClick={() => void submitReject()}>Store rejection</button>
          </>
        }>
        <p className="text-body text-muted mb-3">
          One click, optional - reasons map directly to scoring weights (e.g. “too similar” raises the novelty weight).
        </p>
        <div className="flex flex-wrap gap-2">
          {REJECT_REASONS.map((r) => (
            <button key={r} className={`chip capitalize ${rejectReason === r ? "chip-active" : ""}`}
              onClick={() => setRejectReason(r)}>
              {r.replace(/_/g, " ")}
            </button>
          ))}
        </div>
      </Modal>

      {/* regenerate dialog */}
      <Modal open={regenOpen} onClose={() => setRegenOpen(false)} title="Regenerate all"
        footer={
          <>
            <button className="btn-ghost" onClick={() => setRegenOpen(false)}>Cancel</button>
            <button className="btn-primary" onClick={() => void regenerate()}>
              <RefreshCw className="w-4 h-4" /> Generate a new batch
            </button>
          </>
        }>
        <label className="label" htmlFor="regen-instruction">What to change?</label>
        <input id="regen-instruction" className="input" value={regenInstruction}
          placeholder="more formal, shorter, different angle"
          onChange={(e) => setRegenInstruction(e.target.value)} />
        <p className="helper">The instruction reuses the cached retrieval context (Section 6.4).</p>
      </Modal>
    </div>
  );
}
