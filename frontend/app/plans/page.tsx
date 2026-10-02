"use client";

/* Monthly Plans (Section 3.7): list, create, pause, resume, delete. */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pause, Play, Plus, Trash2 } from "lucide-react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { http } from "@/lib/api";
import { LENGTH_PRESETS, PURPOSES, PLATFORMS, TONES, type Audience, type Plan, type Topic } from "@/lib/types";
import { EmptyState, Modal, StatusChip, fmtDate, useToast } from "@/components/ui";

const planSchema = z.object({
  name: z.string().min(1, "Give the plan a name").max(120),
  topic_id: z.string().nullable(),
  audience_id: z.string().nullable(),
  tones: z.array(z.string()).min(1).max(2),
  purpose: z.string(),
  platform: z.string(),
  length_preset: z.string(),
  cta_text: z.string().max(200).nullable(),
  keywords: z.string(),
  post_day: z.coerce.number().min(1).max(28),
  lead_days: z.coerce.number().min(0).max(28),
});

type PlanForm = z.infer<typeof planSchema>;

export default function PlansPage() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);

  const { data: plans = [], isLoading } = useQuery({
    queryKey: ["plans"],
    queryFn: () => http.get<Plan[]>("/monthly-plans"),
  });
  const { data: audiences = [] } = useQuery({ queryKey: ["audiences"], queryFn: () => http.get<Audience[]>("/audiences") });
  const { data: topics = [] } = useQuery({ queryKey: ["topics"], queryFn: () => http.get<Topic[]>("/topics") });

  const form = useForm<PlanForm>({
    resolver: zodResolver(planSchema),
    defaultValues: {
      name: "", topic_id: null, audience_id: null, tones: ["warm"], purpose: "promote",
      platform: "instagram", length_preset: "medium", cta_text: null, keywords: "",
      post_day: 1, lead_days: 7,
    },
  });
  const { register, handleSubmit, watch, setValue, formState } = form;

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["plans"] });
    void queryClient.invalidateQueries({ queryKey: ["cycles"] });
    void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  };

  const create = useMutation({
    mutationFn: (payload: Record<string, unknown>) => http.post<Plan>("/monthly-plans", payload),
    onSuccess: () => { refresh(); setOpen(false); toast("Plan created - the next 12 cycles are on the calendar."); },
    onError: (e: Error) => toast(e.message, "error"),
  });

  const toggle = useMutation({
    mutationFn: ({ id, path }: { id: string; path: string }) => http.post<Plan>(`/monthly-plans/${id}/${path}`),
    onSuccess: refresh,
    onError: (e: Error) => toast(e.message, "error"),
  });

  const remove = useMutation({
    mutationFn: (id: string) => http.del(`/monthly-plans/${id}`),
    onSuccess: () => { refresh(); toast("Plan deleted."); },
    onError: (e: Error) => toast(e.message, "error"),
  });

  const onSubmit = (data: PlanForm) => {
    create.mutate({
      name: data.name,
      topic_id: data.topic_id || null,
      audience_id: data.audience_id || null,
      tones: data.tones,
      purpose: data.purpose,
      platform: data.platform,
      length_preset: data.length_preset,
      cta_text: data.cta_text || null,
      keywords: data.keywords.split(",").map((k) => k.trim()).filter(Boolean).slice(0, 6),
      post_day: data.post_day,
      lead_days: data.lead_days,
    });
  };

  const tones = watch("tones");

  return (
    <div className="space-y-5 animate-fade">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-display">Monthly Plans</h1>
          <p className="text-body text-muted mt-1">Define once - each month a ready-to-approve shortlist appears.</p>
        </div>
        <button className="btn-primary" onClick={() => setOpen(true)}>
          <Plus className="w-4 h-4" /> New plan
        </button>
      </div>

      {isLoading ? (
        <div className="card p-6 animate-pulse h-32 bg-slate-50" />
      ) : plans.length === 0 ? (
        <EmptyState title="No plans yet"
          subtitle="A plan carries your topic, audience, tones and posting day into every future month."
          action={<button className="btn-primary" onClick={() => setOpen(true)}>Create your first plan</button>} />
      ) : (
        <div className="grid sm:grid-cols-2 gap-4">
          {plans.map((plan) => (
            <article key={plan.id} className="card p-5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h3 className="text-h3 truncate">{plan.name}</h3>
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    {plan.tones.map((t) => (
                      <span key={t} className="badge bg-primary-50 text-primary-700 capitalize">{t}</span>
                    ))}
                    <span className="badge bg-slate-100 text-slate-600 capitalize">{plan.purpose}</span>
                    <span className="badge bg-slate-100 text-slate-600 capitalize">{plan.platform}</span>
                    {!plan.is_active && <span className="badge bg-amber-50 text-amber-700">Paused</span>}
                  </div>
                </div>
                {plan.last_status && <StatusChip status={plan.last_status} />}
              </div>

              <dl className="mt-4 space-y-1.5 text-body">
                <div className="flex justify-between"><dt className="text-muted">Posts on</dt>
                  <dd className="font-medium">day {plan.post_day} · {plan.post_time.slice(0, 5)}</dd></div>
                <div className="flex justify-between"><dt className="text-muted">Generates</dt>
                  <dd className="font-medium">{plan.lead_days} days before</dd></div>
                <div className="flex justify-between"><dt className="text-muted">Next generation</dt>
                  <dd className="font-medium">{plan.next_generate_at ? fmtDate(plan.next_generate_at) : "—"}</dd></div>
                {plan.keywords.length > 0 && (
                  <div className="flex justify-between gap-4"><dt className="text-muted">Keywords</dt>
                    <dd className="font-medium text-right truncate">{plan.keywords.join(", ")}</dd></div>
                )}
              </dl>

              <div className="mt-4 pt-4 border-t border-line flex gap-2">
                <button className="btn-secondary btn-sm"
                  onClick={() => toggle.mutate({ id: plan.id, path: plan.is_active ? "pause" : "resume" })}>
                  {plan.is_active ? <><Pause className="w-4 h-4" /> Pause</> : <><Play className="w-4 h-4" /> Resume</>}
                </button>
                <button className="btn-danger btn-sm"
                  onClick={() => { if (confirm(`Delete “${plan.name}” and its cycles?`)) remove.mutate(plan.id); }}>
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </article>
          ))}
        </div>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title="New Monthly Plan"
        footer={
          <>
            <button className="btn-ghost" onClick={() => setOpen(false)}>Cancel</button>
            <button className="btn-primary" form="plan-form" type="submit" disabled={create.isPending}>
              {create.isPending ? "Creating…" : "Create plan"}
            </button>
          </>
        }>
        <form id="plan-form" onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <div>
            <label className="label" htmlFor="plan-name">Plan name <span className="text-danger">*</span></label>
            <input id="plan-name" className="input" placeholder="Cafe monthly offer" {...register("name")} />
            {formState.errors.name && <p className="helper text-danger">{formState.errors.name.message}</p>}
          </div>

          <div className="grid sm:grid-cols-2 gap-4">
            <div>
              <label className="label" htmlFor="plan-topic">Topic</label>
              <select id="plan-topic" className="input" {...register("topic_id")}>
                <option value="">— none —</option>
                {topics.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
            </div>
            <div>
              <label className="label" htmlFor="plan-audience">Audience</label>
              <select id="plan-audience" className="input" {...register("audience_id")}>
                <option value="">— default —</option>
                {audiences.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            </div>
          </div>

          <div>
            <label className="label">Tones <span className="font-normal text-muted">· up to 2</span></label>
            <div className="flex flex-wrap gap-2">
              {TONES.map((t) => {
                const active = tones.includes(t);
                return (
                  <button key={t} type="button" className={`chip capitalize ${active ? "chip-active" : ""}`}
                    onClick={() => {
                      if (active) { if (tones.length > 1) setValue("tones", tones.filter((x) => x !== t)); }
                      else if (tones.length < 2) setValue("tones", [...tones, t]);
                      else setValue("tones", [tones[1], t]);
                    }}>
                    {t}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="grid sm:grid-cols-3 gap-4">
            <div>
              <label className="label" htmlFor="plan-purpose">Purpose</label>
              <select id="plan-purpose" className="input capitalize" {...register("purpose")}>
                {PURPOSES.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
            <div>
              <label className="label" htmlFor="plan-platform">Platform</label>
              <select id="plan-platform" className="input" {...register("platform")}>
                {PLATFORMS.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
            <div>
              <label className="label" htmlFor="plan-length">Length</label>
              <select id="plan-length" className="input capitalize" {...register("length_preset")}>
                {LENGTH_PRESETS.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
          </div>

          <div className="grid sm:grid-cols-3 gap-4">
            <div>
              <label className="label" htmlFor="plan-day">Posting day</label>
              <input id="plan-day" type="number" min={1} max={28} className="input" {...register("post_day")} />
            </div>
            <div>
              <label className="label" htmlFor="plan-lead">Lead days</label>
              <input id="plan-lead" type="number" min={0} max={28} className="input" {...register("lead_days")} />
              <p className="helper">Generate this many days ahead.</p>
            </div>
            <div>
              <label className="label" htmlFor="plan-cta">CTA</label>
              <input id="plan-cta" className="input" placeholder="Visit us this weekend" {...register("cta_text")} />
            </div>
          </div>

          <div>
            <label className="label" htmlFor="plan-keywords">Keywords <span className="font-normal text-muted">· comma separated, max 6</span></label>
            <input id="plan-keywords" className="input" placeholder="free refill, evening" {...register("keywords")} />
          </div>
        </form>
      </Modal>
    </div>
  );
}
