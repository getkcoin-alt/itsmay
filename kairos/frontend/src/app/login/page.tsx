"use client";

import { Eye, EyeOff, Sparkles } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { AuthLayout } from "@/components/public/auth-layout";
import { Button, ErrorNote, Input } from "@/components/ui";
import { api } from "@/lib/api";
import type { User } from "@/lib/types";

// Public demo-user credentials only. Administrator credentials are never shipped to the browser.
const DEMO = { email: "demo@platform.test", password: "Demo@123456" };

export default function LoginPage() {
  return (
    <Suspense>
      <LoginInner />
    </Suspense>
  );
}

function LoginInner() {
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const login = async (e?: React.FormEvent, creds?: { email: string; password: string }) => {
    e?.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await api<{ user: User }>("/api/auth/login", { method: "POST", json: creds ?? { email, password } });
      const next = params.get("next");
      const safeNext = next && next.startsWith("/") && !next.startsWith("//") ? next : null;
      router.replace(safeNext ?? (r.user.role === "admin" ? "/admin" : String(r.user.preferences?.default_landing ?? "/dashboard")));
    } catch (e2) {
      setErr((e2 as Error).message);
      setBusy(false);
    }
  };

  return (
    <AuthLayout title="Sign in" subtitle="Access your Kairos demo trading account." footer={<>New here? <Link href="/register" className="font-medium text-brand hover:underline">Create a demo account</Link></>}>
      <button
        onClick={() => {
          setEmail(DEMO.email);
          setPassword(DEMO.password);
          login(undefined, DEMO);
        }}
        disabled={busy}
        className="group mb-5 flex w-full items-center gap-3 rounded-xl border border-brand/30 bg-brand-soft px-4 py-3 text-left transition-colors hover:border-brand/60 disabled:opacity-60"
      >
        <Sparkles size={18} className="shrink-0 text-brand" />
        <div className="min-w-0">
          <div className="text-sm font-semibold text-fg">Continue with the demo account</div>
          <div className="truncate text-xs text-fg-muted">{DEMO.email} · ₹10,00,000 simulated capital</div>
        </div>
      </button>
      <div className="mb-5 flex items-center gap-3 text-[11px] uppercase tracking-[0.12em] text-fg-faint">
        <span className="h-px flex-1 bg-ink-700" /> or sign in <span className="h-px flex-1 bg-ink-700" />
      </div>
      <form onSubmit={login} className="space-y-4">
        <Input label="Email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" />
        <Input
          label="Password"
          type={show ? "text" : "password"}
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          suffix={
            <button type="button" onClick={() => setShow((s) => !s)} aria-label={show ? "Hide password" : "Show password"} className="text-fg-muted hover:text-fg">
              {show ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          }
        />
        <div className="flex justify-end">
          <Link href="/forgot-password" className="text-xs font-medium text-brand hover:underline">Forgot password?</Link>
        </div>
        <ErrorNote error={err} />
        <Button type="submit" variant="primary" size="lg" className="w-full" loading={busy}>Sign in</Button>
      </form>
    </AuthLayout>
  );
}
