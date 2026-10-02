"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Sparkles } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api";

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not sign in");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen bg-canvas flex items-center justify-center px-4">
      <div className="w-full max-w-md">
        <div className="flex items-center justify-center gap-2.5 mb-6">
          <div className="w-9 h-9 rounded-xl bg-primary-600 text-white flex items-center justify-center">
            <Sparkles className="w-5 h-5" />
          </div>
          <span className="text-h1">MonthlyMuse</span>
        </div>

        <div className="card p-6 sm:p-8">
          <h1 className="text-h2 mb-1">Welcome back</h1>
          <p className="text-body text-muted mb-6">Sign in to review this month&apos;s candidates.</p>

          {error && (
            <div className="mb-4 rounded-input bg-red-50 border border-danger/30 px-3 py-2 text-body text-red-700">
              {error}
            </div>
          )}

          <form onSubmit={submit} className="space-y-4">
            <div>
              <label className="label" htmlFor="email">Email <span className="sr-only">required</span></label>
              <input id="email" className="input" type="email" required autoComplete="email"
                value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" />
            </div>
            <div>
              <label className="label" htmlFor="password">Password <span className="sr-only">required</span></label>
              <input id="password" className="input" type="password" required autoComplete="current-password"
                value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••••••••" />
            </div>
            <button className="btn-primary w-full" disabled={busy}>
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>

          <div className="mt-5 rounded-input bg-canvas border border-line p-3">
            <p className="text-small text-muted">
              Demo account: <span className="font-mono text-ink">demo@monthlymuse.app</span> /{" "}
              <span className="font-mono text-ink">monthlymuse-demo</span>
            </p>
          </div>

          <p className="text-body text-muted mt-5 text-center">
            No account?{" "}
            <Link href="/register" className="text-primary-700 font-medium hover:underline">Create one</Link>
          </p>
        </div>
      </div>
    </div>
  );
}
