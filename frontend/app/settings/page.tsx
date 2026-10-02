"use client";

/* Settings (Section 3.7): voice & preferences, learned values, notifications, account. */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Download, RotateCcw, Trash2 } from "lucide-react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { http, setToken } from "@/lib/api";
import { LENGTH_PRESETS, type Audience, type LearnedPreferences, type Preferences } from "@/lib/types";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/ui";

const prefsSchema = z.object({
  voice_notes: z.string().max(1000).nullable(),
  banned_words: z.string(),
  emoji_level: z.coerce.number().min(0).max(2),
  length_preset: z.string(),
  lead_days: z.coerce.number().min(0).max(28),
  notify_email: z.boolean(),
  learning_paused: z.boolean(),
  variety: z.coerce.number().min(0.5).max(0.9),
  default_tones: z.array(z.string()).max(2),
  default_platform: z.string(),
  default_language: z.string(),
});

type PrefsForm = z.infer<typeof prefsSchema>;

export default function SettingsPage() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const { user, logout } = useAuth();
  const [audienceName, setAudienceName] = useState("");
  const [audienceDesc, setAudienceDesc] = useState("");

  const { data: prefs } = useQuery({ queryKey: ["preferences"], queryFn: () => http.get<Preferences>("/me/preferences") });
  const { data: learned } = useQuery({ queryKey: ["learned"], queryFn: () => http.get<LearnedPreferences>("/me/preferences/learned") });
  const { data: audiences = [], refetch: refetchAudiences } = useQuery({
    queryKey: ["audiences"], queryFn: () => http.get<Audience[]>("/audiences"),
  });

  const form = useForm<PrefsForm>({
    resolver: zodResolver(prefsSchema),
    defaultValues: {
      voice_notes: null, banned_words: "", emoji_level: 1, length_preset: "medium",
      lead_days: 7, notify_email: true, learning_paused: false, variety: 0.7,
      default_tones: [], default_platform: "instagram", default_language: "en",
    },
  });
  const { register, handleSubmit, watch, reset, formState } = form;

  useEffect(() => {
    if (prefs) {
      reset({
        voice_notes: prefs.voice_notes,
        banned_words: (prefs.banned_words ?? []).join(", "),
        emoji_level: prefs.emoji_level,
        length_preset: prefs.length_preset,
        lead_days: prefs.lead_days,
        notify_email: prefs.notify_email,
        learning_paused: prefs.learning_paused,
        variety: prefs.variety,
        default_tones: [],
        default_platform: prefs.default_platform,
        default_language: prefs.default_language,
      });
    }
  }, [prefs, reset]);

  const save = useMutation({
    mutationFn: (payload: Record<string, unknown>) => http.put<Preferences>("/me/preferences", payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["preferences"] });
      toast("Preferences saved.");
    },
    onError: (e: Error) => toast(e.message, "error"),
  });

  const resetLearning = useMutation({
    mutationFn: () => http.post("/me/preferences/reset-learning"),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["learned"] });
      void queryClient.invalidateQueries({ queryKey: ["preferences"] });
      toast("Personalization cleared - weights, preference vector and arm stats are back to defaults.");
    },
  });

  const addAudience = useMutation({
    mutationFn: () => http.post<Audience>("/audiences", { name: audienceName, description: audienceDesc || null }),
    onSuccess: () => {
      setAudienceName(""); setAudienceDesc("");
      void refetchAudiences();
      toast("Audience saved.");
    },
    onError: (e: Error) => toast(e.message, "error"),
  });

  const removeAudience = useMutation({
    mutationFn: (id: string) => http.del(`/audiences/${id}`),
    onSuccess: () => void refetchAudiences(),
  });

  const onSubmit = (data: PrefsForm) => {
    save.mutate({
      voice_notes: data.voice_notes || null,
      banned_words: data.banned_words.split(",").map((w) => w.trim()).filter(Boolean),
      emoji_level: data.emoji_level,
      length_preset: data.length_preset,
      lead_days: data.lead_days,
      notify_email: data.notify_email,
      learning_paused: data.learning_paused,
      variety: data.variety,
      default_tones: data.default_tones,
      default_platform: data.default_platform,
      default_language: data.default_language,
    });
  };

  const exportData = async () => {
    try {
      const data = await http.get<Record<string, unknown>>("/me/export");
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "monthlymuse-export.json";
      a.click();
      URL.revokeObjectURL(a.href);
      toast("Data export downloaded.");
    } catch {
      toast("Export failed", "error");
    }
  };

  const deleteAccount = async () => {
    if (!confirm("Delete your account and ALL data? This cannot be undone.")) return;
    try {
      await http.del("/me");
      setToken(null);
      await logout();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const weights = learned?.scoring_weights ?? {};
  const weightLabels: Record<string, string> = {
    R: "Relevance", T: "Tone match", P: "Personalization", N: "Novelty",
    Len: "Length suitability", Hist: "Historical performance",
  };

  return (
    <div className="space-y-5 animate-fade max-w-4xl">
      <div>
        <h1 className="text-display">Settings</h1>
        <p className="text-body text-muted mt-1">Your voice, what the system learned, and your data.</p>
      </div>

      {/* voice & preferences */}
      <form onSubmit={handleSubmit(onSubmit)} className="card p-5 space-y-5">
        <h2 className="text-h2">Voice and preferences</h2>

        <div>
          <label className="label" htmlFor="voice">Brand voice notes</label>
          <textarea id="voice" rows={3} className="input h-auto py-2"
            placeholder="Warm neighbourhood cafe. Friendly, never salesy."
            {...register("voice_notes")} />
          <p className="helper">Passed to the prompt as voice guidance.</p>
        </div>

        <div className="grid sm:grid-cols-2 gap-4">
          <div>
            <label className="label" htmlFor="banned">Banned words <span className="font-normal text-muted">· comma separated</span></label>
            <input id="banned" className="input" placeholder="cheap, guaranteed" {...register("banned_words")} />
            <p className="helper">Hard constraint - any occurrence filters the candidate.</p>
          </div>
          <div>
            <label className="label" htmlFor="platform">Default platform</label>
            <select id="platform" className="input" {...register("default_platform")}>
              {["instagram", "x", "linkedin", "facebook", "whatsapp", "email"].map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
          </div>
        </div>

        <div className="grid sm:grid-cols-3 gap-4">
          <div>
            <label className="label" htmlFor="emoji">Emoji level</label>
            <select id="emoji" className="input" {...register("emoji_level")}>
              <option value={0}>0 - none</option>
              <option value={1}>1 - light</option>
              <option value={2}>2 - heavy</option>
            </select>
          </div>
          <div>
            <label className="label" htmlFor="length-preset">Preferred length</label>
            <select id="length-preset" className="input capitalize" {...register("length_preset")}>
              {LENGTH_PRESETS.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
            {learned?.learned_length.n ? (
              <p className="helper">Learned: ~{Math.round(learned.learned_length.mean ?? 0)} words (n={learned.learned_length.n})</p>
            ) : <p className="helper">Not learned yet - appears after your first selection.</p>}
          </div>
          <div>
            <label className="label" htmlFor="lead">Lead days</label>
            <input id="lead" type="number" min={0} max={28} className="input" {...register("lead_days")} />
            <p className="helper">Generate this many days before posting.</p>
          </div>
        </div>

        <div>
          <label className="label" htmlFor="variety">
            More variety: <span className="font-mono">{watch("variety")}</span> (MMR λ)
          </label>
          <input id="variety" type="range" min={0.5} max={0.9} step={0.05} className="w-full accent-primary-600"
            {...register("variety")} />
          <div className="flex justify-between text-small text-muted"><span>Diverse</span><span>On-message</span></div>
        </div>

        <div className="flex flex-wrap gap-4">
          <label className="flex items-center gap-2 text-body">
            <input type="checkbox" className="accent-primary-600 w-4 h-4" {...register("notify_email")} />
            Email notifications
          </label>
          <label className="flex items-center gap-2 text-body">
            <input type="checkbox" className="accent-primary-600 w-4 h-4" {...register("learning_paused")} />
            Pause learning
          </label>
        </div>

        <div className="flex justify-end">
          <button className="btn-primary" disabled={save.isPending || formState.isSubmitting}>
            {save.isPending ? "Saving…" : "Save preferences"}
          </button>
        </div>
      </form>

      {/* learned priorities */}
      <div className="card p-5">
        <div className="flex items-center justify-between gap-3 mb-4">
          <div>
            <h2 className="text-h2">Your learned priorities</h2>
            <p className="text-body text-muted">
              {learned?.n_feedback_events ?? 0} feedback events
              {learned?.learning_paused && " · learning is paused"}
            </p>
          </div>
          <button className="btn-danger btn-sm" onClick={() => { if (confirm("Reset all learned personalization?")) resetLearning.mutate(); }}>
            <RotateCcw className="w-4 h-4" /> Reset learning
          </button>
        </div>

        <div className="grid sm:grid-cols-2 gap-x-8 gap-y-2.5">
          {Object.entries(weights).map(([key, value]) => (
            <div key={key}>
              <div className="flex justify-between text-body mb-1">
                <span className="text-ink-2">{weightLabels[key] ?? key}</span>
                <span className="font-mono">{(value * 100).toFixed(1)}%</span>
              </div>
              <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
                <div className="h-full rounded-full" style={{
                  width: `${value * 100}%`,
                  background: ["#4F46E5", "#0D9488", "#F59E0B", "#DB2777", "#0284C7", "#7C3AED"][Object.keys(weights).indexOf(key) % 6],
                }} />
              </div>
            </div>
          ))}
        </div>

        <div className="grid sm:grid-cols-3 gap-4 mt-5 pt-4 border-t border-line text-body">
          <div>
            <p className="text-small text-muted">Preference vector</p>
            <p className="font-mono">{learned?.preference_vector_norm != null ? `‖u‖ = ${learned.preference_vector_norm}` : "not learned yet"}</p>
          </div>
          <div>
            <p className="text-small text-muted">Learned length</p>
            <p className="font-mono">
              {learned?.learned_length.n
                ? `~${Math.round(learned.learned_length.mean ?? 0)} words ± ${Math.round(learned.learned_length.std ?? 0)}`
                : "—"}
            </p>
          </div>
          <div>
            <p className="text-small text-muted">Tone preferences</p>
            <p className="capitalize">
              {learned?.tone_preferences?.length
                ? learned.tone_preferences.map((t) => `${t.tone} ${Math.round(t.share * 100)}%`).join(" · ")
                : "cold start - exploration active"}
            </p>
          </div>
        </div>

        {(learned?.arm_stats?.length ?? 0) > 0 && (
          <div className="mt-5 pt-4 border-t border-line">
            <p className="text-small font-semibold text-ink mb-2">Style arms (Beta posteriors)</p>
            <div className="overflow-x-auto">
              <table className="w-full text-body">
                <thead>
                  <tr className="text-small text-muted text-left">
                    <th className="py-1.5 pr-3">Arm</th>
                    <th className="py-1.5 pr-3">Mean θ</th>
                    <th className="py-1.5 pr-3">α / β</th>
                    <th className="py-1.5 pr-3">Shown</th>
                    <th className="py-1.5">Selected</th>
                  </tr>
                </thead>
                <tbody>
                  {learned?.arm_stats?.map((arm, i) => (
                    <tr key={i} className="border-t border-line">
                      <td className="py-1.5 pr-3 capitalize">{arm.tone} · {arm.structure.replace("-", " ")}</td>
                      <td className="py-1.5 pr-3 font-mono">{arm.mean.toFixed(2)}</td>
                      <td className="py-1.5 pr-3 font-mono">{arm.alpha.toFixed(1)} / {arm.beta.toFixed(1)}</td>
                      <td className="py-1.5 pr-3 font-mono">{arm.n_shown}</td>
                      <td className="py-1.5 font-mono">{arm.n_selected}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* audiences */}
      <div className="card p-5">
        <h2 className="text-h2 mb-4">Audience profiles</h2>
        <div className="space-y-2 mb-4">
          {audiences.map((a) => (
            <div key={a.id} className="flex items-center gap-3 rounded-input border border-line px-3 py-2">
              <div className="min-w-0 flex-1">
                <p className="text-body font-medium">{a.name}</p>
                {a.description && <p className="text-small text-muted truncate">{a.description}</p>}
              </div>
              <button className="btn-ghost btn-sm !px-2" aria-label={`Delete ${a.name}`}
                onClick={() => removeAudience.mutate(a.id)}>
                <Trash2 className="w-4 h-4 text-danger" />
              </button>
            </div>
          ))}
          {audiences.length === 0 && <p className="text-body text-muted">No audiences yet.</p>}
        </div>
        <div className="flex flex-col sm:flex-row gap-2">
          <input className="input flex-1" placeholder="Young professionals" value={audienceName}
            onChange={(e) => setAudienceName(e.target.value)} aria-label="Audience name" />
          <input className="input flex-1" placeholder="25-35, city workers" value={audienceDesc}
            onChange={(e) => setAudienceDesc(e.target.value)} aria-label="Audience description" />
          <button className="btn-secondary shrink-0" disabled={!audienceName.trim()}
            onClick={() => addAudience.mutate()}>Add audience</button>
        </div>
      </div>

      {/* account & data */}
      <div className="card p-5">
        <h2 className="text-h2 mb-3">Account and data</h2>
        <dl className="space-y-2 text-body mb-4">
          <div className="flex justify-between"><dt className="text-muted">Signed in as</dt><dd className="font-medium">{user?.email}</dd></div>
          <div className="flex justify-between"><dt className="text-muted">Timezone</dt><dd className="font-medium">{user?.timezone}</dd></div>
          <div className="flex justify-between"><dt className="text-muted">Region</dt><dd className="font-medium">{user?.region}</dd></div>
        </dl>
        <div className="flex flex-wrap gap-2 pt-4 border-t border-line">
          <button className="btn-secondary" onClick={() => void exportData()}>
            <Download className="w-4 h-4" /> Export all data (JSON)
          </button>
          <button className="btn-danger" onClick={() => void deleteAccount()}>
            <AlertTriangle className="w-4 h-4" /> Delete account
          </button>
        </div>
      </div>
    </div>
  );
}
