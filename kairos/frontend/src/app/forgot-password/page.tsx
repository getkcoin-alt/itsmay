"use client";

import { CheckCircle2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { AuthLayout } from "@/components/public/auth-layout";
import { Button, ErrorNote, Input } from "@/components/ui";
import { api } from "@/lib/api";

export default function ForgotPage() {
  return (
    <Suspense>
      <ForgotInner />
    </Suspense>
  );
}

function ForgotInner() {
  const token = useSearchParams().get("token");
  return token ? <ResetForm token={token} /> : <RequestForm />;
}

function RequestForm() {
  const [email, setEmail] = useState("");
  const [res, setRes] = useState<{ message: string; demo_reset_link?: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      setRes(await api("/api/auth/forgot-password", { method: "POST", json: { email } }));
    } catch (e2) {
      setErr((e2 as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <AuthLayout title="Reset your password" subtitle="We’ll issue a single-use link valid for 30 minutes." footer={<Link href="/login" className="font-medium text-brand hover:underline">Back to sign in</Link>}>
      {res ? (
        <div className="space-y-3 text-sm">
          <p className="flex gap-2 text-fg-soft"><CheckCircle2 size={17} className="mt-0.5 shrink-0 text-up" /> {res.message}</p>
          {res.demo_reset_link && (
            <div className="rounded-lg border border-warn/30 bg-warn-soft p-3 text-[13px] text-[#F7D79B]">
              This demo has no outbound email, so the link is shown here:
              <Link href={res.demo_reset_link} className="mt-2 block break-all font-medium text-brand underline">Open password reset link</Link>
            </div>
          )}
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          <Input label="Email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          <ErrorNote error={err} />
          <Button type="submit" variant="primary" size="lg" className="w-full" loading={busy}>Send reset link</Button>
        </form>
      )}
    </AuthLayout>
  );
}

function ResetForm({ token }: { token: string }) {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await api("/api/auth/reset-password", { method: "POST", json: { token, password } });
      setDone(true);
      setTimeout(() => router.replace("/login"), 1500);
    } catch (e2) {
      setErr((e2 as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <AuthLayout title="Choose a new password">
      {done ? (
        <p className="flex gap-2 text-sm text-fg-soft"><CheckCircle2 size={17} className="text-up" /> Password updated. Redirecting to sign in…</p>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          <Input label="New password" type="password" required value={password} onChange={(e) => setPassword(e.target.value)} hint="8+ characters with upper & lower case, a number and a symbol" />
          <ErrorNote error={err} />
          <Button type="submit" variant="primary" size="lg" className="w-full" loading={busy}>Update password</Button>
        </form>
      )}
    </AuthLayout>
  );
}
