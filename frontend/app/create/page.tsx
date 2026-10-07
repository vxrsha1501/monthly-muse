"use client";

/* Create Monthly Post: 3-step form with Zod validation + draft autosave (Section 3.2). */
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Check, Plus, Sparkles, Wand2, X } from "lucide-react";
import { http } from "@/lib/api";
import {
  LENGTH_PRESETS, PURPOSES, PLATFORMS, TONES,
  type Audience, type GenerateResponse, type LearnedPreferences,
  type Occasion, type Topic,
} from "@/lib/types";
import { useToast } from "@/components/ui";

const schema = z.object({
  month: z.string().regex(/^\d{4}-(0[1-9]|1[0-2])$/, "Pick a month"),
  topic_id: z.string().nullable().optional(),
  topic: z.string().max(120).optional(),
  occasion: z.string().max(120).nullable().optional(),
  audience_id: z.string().nullable().optional(),
  tones: z.array(z.string()).min(1, "Pick at least one tone").max(2, "Up to 2 tones"),
  purpose: z.string(),
  platform: z.string(),
  length: z.string(),
  language: z.string(),
  keywords: z.array(z.string()).max(6),
  cta: z.string().max(200).nullable().optional(),
  additional_instructions: z.string().max(500).nullable().optional(),
}).superRefine((v, ctx) => {
  // Blueprint: topic is required - either a saved topic or a typed one.
  if (!v.topic?.trim() && !v.topic_id) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      path: ["topic"],
      message: "Topic is required - pick a saved topic or type one",
    });
  }
});

type FormValues = z.infer<typeof schema>;

const DRAFT_KEY = "mm_draft_post";

function nextMonth(): string {
  const d = new Date();
  const idx = d.getFullYear() * 12 + d.getMonth() + 1;
  const y = Math.floor(idx / 12);
  const m = (idx % 12) + 1;
  return `${y}-${String(m).padStart(2, "0")}`;
}

function monthLabel(month: string): string {
  const [y, m] = month.split("-");
  return new Date(Number(y), Number(m) - 1, 1).toLocaleDateString("en-GB", { month: "long", year: "numeric" });
}

export default function CreatePage() {
  const router = useRouter();
  const toast = useToast();
  const [step, setStep] = useState(0);
  const [keywordDraft, setKeywordDraft] = useState("");

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      month: nextMonth(),
      topic: "", topic_id: null, occasion: null, audience_id: null,
      tones: ["warm"], purpose: "inform", platform: "instagram",
      length: "medium", language: "en", keywords: [], cta: "",
      additional_instructions: "",
    },
    mode: "onTouched",
  });

  const { register, handleSubmit, watch, setValue, getValues, trigger, formState } = form;
  const values = watch();

  /* ---- data ---- */
  const { data: audiences } = useQuery({ queryKey: ["audiences"], queryFn: () => http.get<Audience[]>("/audiences") });
  const { data: topics } = useQuery({ queryKey: ["topics"], queryFn: () => http.get<Topic[]>("/topics") });
  const { data: learned } = useQuery({ queryKey: ["learned"], queryFn: () => http.get<LearnedPreferences>("/me/preferences/learned") });
  const monthNumber = Number(values.month?.split("-")[1] ?? "1");
  const { data: occasions } = useQuery({
    queryKey: ["occasions", monthNumber],
    queryFn: () => http.get<Occasion[]>(`/occasions?month=${monthNumber}`),
  });
  const { data: historyTotal } = useQuery({
    queryKey: ["history-count"],
    queryFn: () => http.get<{ total: number }>("/messages?page_size=1"),
  });

  /* ---- draft autosave every 5s ---- */
  useEffect(() => {
    try {
      const saved = localStorage.getItem(DRAFT_KEY);
      if (saved) {
        const parsed = JSON.parse(saved) as Partial<FormValues>;
        if (parsed && parsed.month) Object.entries(parsed).forEach(([k, v]) => setValue(k as keyof FormValues, v as never));
      }
    } catch { /* ignore */ }
    const id = setInterval(() => {
      try { localStorage.setItem(DRAFT_KEY, JSON.stringify(getValues())); } catch { /* ignore */ }
    }, 5000);
    return () => clearInterval(id);
  }, [setValue, getValues]); // eslint-disable-line react-hooks/exhaustive-deps

  /* ---- generation ---- */
  const generate = useMutation({
    mutationFn: (payload: Record<string, unknown>) => http.post<GenerateResponse>("/messages/generate", payload),
    onSuccess: (data) => {
      localStorage.removeItem(DRAFT_KEY);
      router.push(`/review/${data.request_id}`);
    },
    onError: (err: Error) => toast(err.message, "error"),
  });

  const recommended = useMemo(() => {
    const tones = learned?.tone_preferences ?? [];
    if (!tones.length) return null;
    const top = [...tones].sort((a, b) => b.share - a.share)[0];
    return top.share > 0 ? top : null;
  }, [learned]);

  /* ---- steps ---- */
  const steps = ["Context", "Style", "Review & Generate"];

  const goNext = async () => {
    const fields: (keyof FormValues)[][] = [
      ["month", "topic", "purpose"],
      ["tones", "length", "platform"],
      [],
    ];
    const ok = await trigger(fields[step], { shouldFocus: true });
    if (ok) setStep((s) => Math.min(s + 1, 2));
  };

  const onSubmit = (data: FormValues) => {
    generate.mutate({
      month: data.month,
      topic_id: data.topic_id ?? undefined,
      topic: data.topic?.trim() || undefined,
      // null (untouched/deselected) = omit -> backend auto-pulls the month's top
      // occasion; "" = the user explicitly chose None; otherwise the picked name.
      occasion: data.occasion ?? undefined,
      audience_id: data.audience_id ?? undefined,
      tones: data.tones,
      purpose: data.purpose,
      platform: data.platform,
      length: data.length,
      language: data.language,
      keywords: data.keywords,
      cta: data.cta || undefined,
      additional_instructions: data.additional_instructions || undefined,
    });
  };

  const toggleTone = (tone: string) => {
    const current = getValues("tones");
    if (current.includes(tone)) {
      if (current.length > 1) setValue("tones", current.filter((t) => t !== tone), { shouldValidate: true });
    } else if (current.length < 2) {
      setValue("tones", [...current, tone], { shouldValidate: true });
    } else {
      setValue("tones", [current[1], tone], { shouldValidate: true });
    }
  };

  const addKeyword = () => {
    const kw = keywordDraft.trim();
    if (!kw) return;
    const current = getValues("keywords");
    if (current.length >= 6) { toast("Up to 6 keywords", "info"); return; }
    if (!current.includes(kw)) setValue("keywords", [...current, kw], { shouldValidate: true });
    setKeywordDraft("");
  };

  return (
    <div className="max-w-3xl mx-auto space-y-5 animate-fade">
      <div>
        <h1 className="text-display">Create monthly post</h1>
        <p className="text-body text-muted mt-1">Three steps - or use a plan&apos;s defaults for a repeat post.</p>
      </div>

      {/* stepper */}
      <ol className="flex items-center gap-2" aria-label="Form steps">
        {steps.map((label, i) => (
          <li key={label} className="flex items-center gap-2 flex-1">
            <button
              type="button"
              onClick={() => i < step && setStep(i)}
              className={`flex items-center gap-2 text-body rounded-input px-2 py-1.5 flex-1 text-left transition-colors
                ${i === step ? "text-primary-700 font-medium bg-primary-50" : i < step ? "text-ink-2 hover:bg-canvas" : "text-muted"}`}
              aria-current={i === step ? "step" : undefined}
            >
              <span className={`w-6 h-6 rounded-full flex items-center justify-center text-small font-semibold shrink-0
                ${i === step ? "bg-primary-600 text-white" : i < step ? "bg-success text-white" : "bg-slate-200 text-slate-600"}`}>
                {i < step ? <Check className="w-3.5 h-3.5" /> : i + 1}
              </span>
              <span className="hidden sm:inline">{label}</span>
            </button>
            {i < steps.length - 1 && <div className="h-px bg-line flex-1 max-w-10" />}
          </li>
        ))}
      </ol>

      <form onSubmit={handleSubmit(onSubmit)} className="card p-5 sm:p-6 space-y-5">
        {/* ---------------- step 1: context ---------------- */}
        {step === 0 && (
          <div className="space-y-5">
            <div>
              <label className="label" htmlFor="month">Month <span className="text-danger">*</span></label>
              <input id="month" type="month" className="input" {...register("month")} />
              <p className="helper">{monthLabel(values.month ?? nextMonth())}</p>
            </div>

            <div>
              <label className="label" htmlFor="topic">Topic <span className="text-danger">*</span></label>
              <select
                className="input" value={values.topic_id ?? ""}
                onChange={(e) => {
                  const id = e.target.value;
                  setValue("topic_id", id || null, { shouldValidate: true });
                  const found = (topics ?? []).find((t) => t.id === id);
                  if (found) {
                    setValue("topic", found.name, { shouldValidate: true });
                    if (found.keywords?.length) setValue("keywords", found.keywords.slice(0, 6));
                  }
                }}
              >
                <option value="">— choose a saved topic —</option>
                {(topics ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
              <input
                className="input mt-2" placeholder="…or type a topic: Evening coffee loyalty rewards"
                value={values.topic ?? ""} onChange={(e) => setValue("topic", e.target.value, { shouldValidate: true })}
              />
              {formState.errors.topic && <p className="helper text-danger">{formState.errors.topic.message}</p>}
            </div>

            <div>
              <label className="label">Occasion <span className="font-normal text-muted">(suggested for this month)</span></label>
              <div className="flex flex-wrap gap-2">
                <button type="button" className={`chip ${values.occasion === "" ? "chip-active" : ""}`}
                  onClick={() => setValue("occasion", "")}>None</button>
                {(occasions ?? []).slice(0, 8).map((o) => (
                  <button key={o.id} type="button"
                    className={`chip ${values.occasion === o.name ? "chip-active" : ""} max-w-64`}
                    title={o.description ?? undefined}
                    onClick={() => setValue("occasion", values.occasion === o.name ? null : o.name)}>
                    <span className="truncate">{o.name}</span>
                  </button>
                ))}
              </div>
              {(occasions ?? [])[0]?.description && values.occasion && (
                <p className="helper">
                  Fact passed to the AI: {occasions?.find((o) => o.name === values.occasion)?.description}
                </p>
              )}
            </div>

            <div>
              <label className="label" htmlFor="audience">Audience <span className="text-danger">*</span></label>
              <select id="audience" className="input" value={values.audience_id ?? ""}
                onChange={(e) => setValue("audience_id", e.target.value || null, { shouldValidate: true })}>
                <option value="">— default (my community) —</option>
                {(audiences ?? []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
              <p className="helper">Affects vocabulary, tone prior and the prompt. Manage audiences in Settings.</p>
            </div>

            <div>
              <label className="label" htmlFor="purpose">Message purpose <span className="text-danger">*</span></label>
              <select id="purpose" className="input capitalize" {...register("purpose")}>
                {PURPOSES.map((p) => <option key={p} value={p} className="capitalize">{p}</option>)}
              </select>
              <p className="helper">Changes the structure of the message.</p>
            </div>
          </div>
        )}

        {/* ---------------- step 2: style ---------------- */}
        {step === 1 && (
          <div className="space-y-5">
            <div>
              <label className="label">
                Tone <span className="text-danger">*</span> <span className="font-normal text-muted">· up to 2</span>
              </label>
              <div className="flex flex-wrap gap-2">
                {TONES.map((tone) => {
                  const active = values.tones?.includes(tone);
                  const recommendedTone = recommended?.tone === tone;
                  return (
                    <button key={tone} type="button"
                      className={`chip capitalize ${active ? "chip-active" : ""} ${recommendedTone ? "border-dashed" : ""}`}
                      onClick={() => toggleTone(tone)}
                      aria-pressed={active}>
                      {tone}
                      {recommendedTone && (
                        <span className="text-primary-700 font-semibold">
                          · Recommended ({Math.round(recommended.share * 100)}%)
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
              {formState.errors.tones && <p className="helper text-danger">{formState.errors.tones.message}</p>}
              <p className="helper">
                Pre-selection comes from smoothed Bayesian counts over your history (Section 5.1.3).
              </p>
            </div>

            <div>
              <label className="label">Length</label>
              <div className="inline-flex rounded-input border border-line overflow-hidden">
                {LENGTH_PRESETS.map((p) => (
                  <button key={p} type="button"
                    onClick={() => setValue("length", p, { shouldValidate: true })}
                    className={`px-4 h-10 text-body capitalize transition-colors
                      ${values.length === p ? "bg-primary-600 text-white" : "bg-white text-ink-2 hover:bg-canvas"}`}>
                    {p}
                  </button>
                ))}
              </div>
              <p className="helper">
                Word range checked as a hard constraint (±15%).
                {learned?.learned_length.n ? ` Your learned length: ~${Math.round(learned.learned_length.mean ?? 0)} words.` : ""}
              </p>
            </div>

            <div className="grid sm:grid-cols-2 gap-4">
              <div>
                <label className="label" htmlFor="platform">Platform</label>
                <select id="platform" className="input" {...register("platform")}>
                  {PLATFORMS.map((p) => <option key={p} value={p}>{p}</option>)}
                </select>
                <p className="helper">Sets the character limit and formatting norms.</p>
              </div>
              <div>
                <label className="label" htmlFor="cta">Call-to-action <span className="font-normal text-muted">(optional)</span></label>
                <input id="cta" className="input" placeholder="Visit us this weekend"
                  {...register("cta")} />
                <p className="helper">Checked programmatically in the output.</p>
              </div>
            </div>

            <div>
              <label className="label" htmlFor="keyword-input">Keywords <span className="font-normal text-muted">· up to 6</span></label>
              <div className="flex gap-2">
                <input id="keyword-input" className="input" value={keywordDraft}
                  placeholder="free refill" onChange={(e) => setKeywordDraft(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addKeyword(); } }} />
                <button type="button" className="btn-secondary shrink-0" onClick={addKeyword}>
                  <Plus className="w-4 h-4" /> Add
                </button>
              </div>
              <div className="flex flex-wrap gap-2 mt-2">
                {values.keywords?.map((kw) => (
                  <span key={kw} className="chip chip-active">
                    {kw}
                    <button type="button" aria-label={`Remove ${kw}`}
                      onClick={() => setValue("keywords", values.keywords.filter((k) => k !== kw))}>
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </span>
                ))}
              </div>
              <p className="helper">Must appear (or be semantically covered) in at least half the candidates.</p>
            </div>
          </div>
        )}

        {/* ---------------- step 3: review ---------------- */}
        {step === 2 && (
          <div className="space-y-5">
            <div className="rounded-card border border-line p-4 bg-canvas">
              <h3 className="text-h3 mb-3">Summary</h3>
              <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-body">
                <div className="flex justify-between gap-3"><dt className="text-muted">Month</dt><dd className="font-medium">{monthLabel(values.month ?? nextMonth())}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-muted">Topic</dt><dd className="font-medium text-right">{values.topic || "—"}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-muted">Occasion</dt><dd className="font-medium text-right">
                  {values.occasion === null || values.occasion === undefined
                    ? (occasions?.[0] ? "Auto (top suggestion)" : "Auto")
                    : (values.occasion || "None")}
                </dd></div>
                <div className="flex justify-between gap-3"><dt className="text-muted">Audience</dt><dd className="font-medium text-right">{(audiences ?? []).find((a) => a.id === values.audience_id)?.name ?? "Default"}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-muted">Tones</dt><dd className="font-medium capitalize">{values.tones?.join(" + ")}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-muted">Purpose</dt><dd className="font-medium capitalize">{values.purpose}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-muted">Platform</dt><dd className="font-medium capitalize">{values.platform}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-muted">Length</dt><dd className="font-medium capitalize">{values.length}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-muted">Keywords</dt><dd className="font-medium text-right">{values.keywords?.join(", ") || "—"}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-muted">CTA</dt><dd className="font-medium text-right">{values.cta || "—"}</dd></div>
              </dl>
            </div>

            <div className="rounded-card border border-primary-200 bg-primary-50/50 p-4">
              <h3 className="text-h3 mb-2 flex items-center gap-2"><Sparkles className="w-4 h-4 text-primary-600" /> What the AI will use</h3>
              <ul className="text-body text-ink-2 space-y-1.5">
                <li>· {historyTotal?.total ?? 0} past post{historyTotal?.total === 1 ? "" : "s"} retrieved for voice and novelty checks</li>
                <li>· Occasion facts for {monthLabel(values.month ?? nextMonth())} {values.occasion ? `(${values.occasion})` : values.occasion === "" ? "" : "(auto-selected top suggestion)"}</li>
                <li>· Your preference profile{(learned?.arm_stats.length ?? 0) > 0 ? ` and ${learned?.arm_stats.length} learned style arms` : " (cold start - exploration active)"}</li>
                <li>· Three style arms chosen by Thompson sampling, 6 candidates generated, top 3 shown</li>
              </ul>
            </div>

            <details className="rounded-card border border-line">
              <summary className="px-4 py-3 cursor-pointer text-body font-medium">Advanced</summary>
              <div className="px-4 pb-4">
                <label className="label" htmlFor="advanced">Additional instructions</label>
                <textarea id="advanced" className="input h-auto py-2 min-h-20" rows={3}
                  placeholder="mention the new patio area; keep it under two sentences" {...register("additional_instructions")} />
                <p className="helper">Treated as untrusted data inside a delimited block (max 500 chars).</p>
                {formState.errors.additional_instructions && (
                  <p className="helper text-danger">{formState.errors.additional_instructions.message}</p>
                )}
              </div>
            </details>
          </div>
        )}

        {/* nav */}
        <div className="flex items-center justify-between pt-2 border-t border-line">
          <button type="button" className="btn-ghost" onClick={() => setStep((s) => Math.max(0, s - 1))}
            disabled={step === 0}>
            <ArrowLeft className="w-4 h-4" /> Back
          </button>
          {step < 2 ? (
            <button type="button" className="btn-primary" onClick={() => void goNext()}>
              Continue <ArrowRight className="w-4 h-4" />
            </button>
          ) : (
            /* Always type=button: a type=button -> type=submit mutation lets the
               browser's deferred click activation submit the form immediately,
               skipping this step. Submit explicitly instead. */
            <button type="button" className="btn-primary" disabled={generate.isPending}
              onClick={() => void handleSubmit(onSubmit)()}>
              <Wand2 className="w-4 h-4" />
              {generate.isPending ? "Generating…" : "Generate 3 candidates"}
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
