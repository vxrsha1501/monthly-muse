"use client";

/* Shared UI primitives (Section 12.4): badges, score ring, empty states, toasts, modal. */
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { AlertCircle, Check, Info, X } from "lucide-react";

/* ------------------------------------------------------------------ badges */

const STATUS_STYLES: Record<string, string> = {
  PLANNED: "bg-slate-100 text-slate-600",
  GENERATING: "bg-sky-50 text-sky-700",
  AWAITING_REVIEW: "bg-amber-50 text-amber-700",
  SCHEDULED: "bg-primary-50 text-primary-700",
  PUBLISHED: "bg-emerald-50 text-emerald-700",
  SKIPPED: "bg-slate-100 text-slate-500",
  GENERATION_FAILED: "bg-red-50 text-red-700",
  NEEDS_ATTENTION: "bg-red-50 text-red-700",
  SELECTED: "bg-emerald-50 text-emerald-700",
  SHOWN: "bg-slate-100 text-slate-600",
  EDITED: "bg-sky-50 text-sky-700",
  REJECTED: "bg-red-50 text-red-700",
  FILTERED: "bg-slate-100 text-slate-400",
  ARCHIVED: "bg-slate-100 text-slate-400",
};

export function StatusChip({ status }: { status: string }) {
  const style = STATUS_STYLES[status] ?? "bg-slate-100 text-slate-600";
  return (
    <span className={`badge ${style}`}>
      {status === "GENERATING" && <span className="w-1.5 h-1.5 rounded-full bg-sky-500 animate-pulseDot" />}
      {status.replace(/_/g, " ")}
    </span>
  );
}

const CONFIDENCE_STYLES: Record<string, string> = {
  high: "bg-emerald-50 text-emerald-700",
  medium: "bg-amber-50 text-amber-700",
  low: "bg-slate-100 text-slate-600",
};

export function ConfidenceChip({ level }: { level: string }) {
  return (
    <span
      className={`badge ${CONFIDENCE_STYLES[level] ?? CONFIDENCE_STYLES.medium}`}
      title="How often this ranked first when we varied the scoring weights slightly"
    >
      {level === "high" ? "High" : level === "medium" ? "Medium" : "Low"} confidence
    </span>
  );
}

const BAND_STYLES: Record<string, string> = {
  Fresh: "bg-emerald-50 text-emerald-700",
  Related: "bg-sky-50 text-sky-700",
  Similar: "bg-amber-50 text-amber-700",
  Repetitive: "bg-red-50 text-red-700",
};

export function SimilarityBadge({ band, cosine }: { band: string; cosine: number | null }) {
  return (
    <span className={`badge ${BAND_STYLES[band] ?? BAND_STYLES.Fresh}`}>
      {cosine !== null ? `${Math.round(cosine * 100)}% similar to your history` : "No history yet"} · {band}
    </span>
  );
}

/* --------------------------------------------------------------- score ring */

export function ScoreRing({ score, size = 56 }: { score: number; size?: number }) {
  const value = Math.round((score ?? 0) * 100);
  const r = (size - 8) / 2;
  const c = 2 * Math.PI * r;
  const color = value >= 75 ? "#059669" : value >= 60 ? "#4F46E5" : "#F59E0B";
  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90" role="img" aria-label={`Score ${value} of 100`}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#E2E8F0" strokeWidth="4" />
        <circle
          cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth="4"
          strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c - (c * value) / 100}
        />
      </svg>
      <span className="absolute font-mono text-small font-semibold text-ink">{value}</span>
    </div>
  );
}

export function FeatureBar({ label, value }: { label: string; value: number }) {
  const pct = Math.round((value ?? 0) * 100);
  return (
    <div className="flex items-center gap-2">
      <span className="w-28 shrink-0 text-small text-ink-2 capitalize">{label}</span>
      <div className="flex-1 h-2 rounded-full bg-slate-100 overflow-hidden">
        <div className="h-full rounded-full bg-primary-600 transition-all duration-300" style={{ width: `${pct}%` }} />
      </div>
      <span className="w-9 text-right font-mono text-small text-muted">{pct}</span>
    </div>
  );
}

/* ------------------------------------------------------------ empty / error */

export function EmptyState({
  title, subtitle, action,
}: { title: string; subtitle?: string; action?: React.ReactNode }) {
  return (
    <div className="card p-10 text-center animate-fade">
      <div className="mx-auto w-12 h-12 rounded-full bg-primary-50 flex items-center justify-center mb-4">
        <Info className="w-6 h-6 text-primary-600" />
      </div>
      <h3 className="text-h3 mb-1">{title}</h3>
      {subtitle && <p className="text-body text-muted max-w-md mx-auto">{subtitle}</p>}
      {action && <div className="mt-5 flex justify-center">{action}</div>}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="card p-6 border-danger/30 bg-red-50/40 animate-fade">
      <div className="flex gap-3">
        <AlertCircle className="w-5 h-5 text-danger shrink-0 mt-0.5" />
        <div className="flex-1">
          <p className="text-body font-medium text-ink">Something didn&apos;t work</p>
          <p className="text-body text-ink-2 mt-0.5">{message}</p>
        </div>
        {onRetry && (
          <button className="btn-secondary btn-sm" onClick={onRetry}>Retry</button>
        )}
      </div>
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-input bg-slate-100 ${className}`} />;
}

export function CardSkeleton() {
  return (
    <div className="card p-5 space-y-3">
      <Skeleton className="h-4 w-1/3" />
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-4 w-1/2" />
    </div>
  );
}

/* ------------------------------------------------------------------ toasts */

type Toast = { id: number; message: string; kind: "success" | "error" | "info" };
const ToastContext = createContext<(message: string, kind?: Toast["kind"]) => void>(() => {});

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = useCallback((message: string, kind: Toast["kind"] = "success") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, message, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3600);
  }, []);

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2" aria-live="polite">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`card px-4 py-3 flex items-center gap-2.5 shadow-lift animate-fade max-w-sm ${
              t.kind === "error" ? "border-danger/40" : ""
            }`}
          >
            {t.kind === "success" && <Check className="w-4 h-4 text-success shrink-0" />}
            {t.kind === "error" && <AlertCircle className="w-4 h-4 text-danger shrink-0" />}
            {t.kind === "info" && <Info className="w-4 h-4 text-info shrink-0" />}
            <span className="text-body text-ink">{t.message}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}

/* ------------------------------------------------------------------- modal */

export function Modal({
  open, onClose, title, children, footer,
}: {
  open: boolean; onClose: () => void; title: string;
  children: React.ReactNode; footer?: React.ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex items-end sm:items-center justify-center p-0 sm:p-6">
      <div className="absolute inset-0 bg-ink/30 backdrop-blur-[1px]" onClick={onClose} aria-hidden />
      <div
        role="dialog" aria-modal="true" aria-label={title}
        className="relative w-full sm:max-w-lg bg-surface rounded-t-modal sm:rounded-modal shadow-lift border border-line animate-fade max-h-[85vh] overflow-y-auto"
      >
        <div className="flex items-center justify-between px-6 pt-5 pb-3">
          <h3 className="text-h3">{title}</h3>
          <button className="btn-ghost btn-sm !px-2" onClick={onClose} aria-label="Close dialog">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="px-6 pb-5">{children}</div>
        {footer && <div className="px-6 py-4 border-t border-line flex justify-end gap-2">{footer}</div>}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------- formatting */

export function pct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function fmtDate(value: string | null | undefined, opts?: Intl.DateTimeFormatOptions): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("en-GB", opts ?? { day: "numeric", month: "short", year: "numeric" });
}
