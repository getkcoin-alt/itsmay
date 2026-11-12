"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AuthLayout } from "@/components/public/auth-layout";
import { Button, ErrorNote, Input } from "@/components/ui";
import { api } from "@/lib/api";

export default function RegisterPage() {
  const router = useRouter();
  const [f, setF] = useState({ full_name: "", email: "", phone: "", password: "", accept_terms: false });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const checks = [
    [f.password.length >= 8, "8+ characters"],
    [/[A-Z]/.test(f.password) && /[a-z]/.test(f.password), "Upper & lower case"],
    [/\d/.test(f.password), "A number"],
    [/[^A-Za-z0-9]/.test(f.password), "A symbol"],
  ] as const;
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await api("/api/auth/register", { method: "POST", json: { ...f, phone: f.phone || null } });
      router.replace("/dashboard");
    } catch (e2) {
      setErr((e2 as Error).message);
      setBusy(false);
    }
  };
  return (
    <AuthLayout title="Create a demo account" subtitle="Start with ₹10,00,000 of simulated capital. No payment details required." footer={<>Already registered? <Link href="/login" className="font-medium text-brand hover:underline">Sign in</Link></>}>
      <form onSubmit={submit} className="space-y-4">
        <Input label="Full name" required value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} autoComplete="name" />
        <Input label="Email" type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} autoComplete="email" />
        <Input label="Mobile (optional)" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} autoComplete="tel" />
        <div>
          <Input label="Password" type="password" required value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} autoComplete="new-password" />
          <ul className="mt-2 grid grid-cols-2 gap-1 text-[11px]">
            {checks.map(([ok, label]) => <li key={label} className={ok ? "text-up" : "text-fg-faint"}>{ok ? "✓" : "○"} {label}</li>)}
          </ul>
        </div>
        <label className="flex items-start gap-2.5 text-[13px] text-fg-soft">
          <input type="checkbox" className="mt-0.5 accent-[#8C9BFF]" checked={f.accept_terms} onChange={(e) => setF({ ...f, accept_terms: e.target.checked })} />
          I understand this is a simulated demonstration: prices are not real, no real orders are placed and no real money is involved.
        </label>
        <ErrorNote error={err} />
        <Button type="submit" variant="primary" size="lg" className="w-full" loading={busy} disabled={!f.accept_terms}>Create account</Button>
      </form>
    </AuthLayout>
  );
}
