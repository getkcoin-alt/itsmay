"use client";

import { useEffect, useState } from "react";
import { useSession } from "@/components/layout/session";
import { Button, Card, ErrorNote, Input, PageHeader, Select, Skeleton, Toggle } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api } from "@/lib/api";

export default function SettingsPage() {
  const { user, refresh } = useSession();
  const toast = useToast();
  const [prefs, setPrefs] = useState<Record<string, unknown>>({});
  const [pw, setPw] = useState({ current_password: "", new_password: "" });
  const [pwErr, setPwErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (user) setPrefs({ default_order_type: "MARKET", confirm_orders: true, compact_numbers: false, default_landing: "/dashboard", ...user.preferences });
  }, [user]);
  if (!user) return <Skeleton className="h-96 rounded-2xl" />;
  const shared = user.email === "demo@platform.test" || user.email === "admin@platform.test";

  const savePrefs = async (next: Record<string, unknown>) => {
    setPrefs(next);
    try {
      await api("/api/profile", { method: "PATCH", json: { preferences: next } });
      toast({ kind: "success", title: "Preferences saved" });
      refresh();
    } catch (e) {
      toast({ kind: "error", title: "Could not save", body: (e as Error).message });
    }
  };

  const changePw = async () => {
    setBusy(true);
    setPwErr(null);
    try {
      await api("/api/auth/change-password", { method: "POST", json: pw });
      toast({ kind: "success", title: "Password changed" });
      setPw({ current_password: "", new_password: "" });
    } catch (e) {
      setPwErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      <PageHeader title="Settings" subtitle="Trading preferences and security." />
      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Trading preferences">
          <div className="space-y-5">
            <Select label="Default landing page" value={String(prefs.default_landing ?? "/dashboard")} onChange={(e) => savePrefs({ ...prefs, default_landing: e.target.value })}>
              <option value="/dashboard">Dashboard</option>
              <option value="/markets">Markets</option>
              <option value="/options">Options</option>
              <option value="/portfolio">Portfolio</option>
            </Select>
            <Select label="Default order type" value={String(prefs.default_order_type ?? "MARKET")} onChange={(e) => savePrefs({ ...prefs, default_order_type: e.target.value })}>
              <option value="MARKET">Market</option>
              <option value="LIMIT">Limit</option>
            </Select>
            <div className="flex items-center justify-between gap-4">
              <div>
                <div className="text-sm font-medium text-fg">Review step before every order</div>
                <div className="text-xs text-fg-muted">Always enabled in the demo so risk disclosures are shown.</div>
              </div>
              <Toggle checked disabled onChange={() => {}} label="Order review" />
            </div>
          </div>
        </Card>
        <Card title="Security">
          {shared ? (
            <p className="text-sm text-fg-soft">The password of the shared demonstration account cannot be changed. Register your own demo account to try the password-change and reset flows.</p>
          ) : (
            <div className="space-y-4">
              <Input label="Current password" type="password" autoComplete="current-password" value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} />
              <Input label="New password" type="password" autoComplete="new-password" value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} hint="8+ characters with upper & lower case, a number and a symbol" />
              <ErrorNote error={pwErr} />
              <Button variant="primary" loading={busy} onClick={changePw}>Change password</Button>
            </div>
          )}
          <div className="mt-5 border-t border-ink-700 pt-4 text-xs text-fg-muted">
            Sessions use httpOnly, SameSite cookies with server-side revocation. Passwords are hashed with Argon2id.
          </div>
        </Card>
      </div>
    </div>
  );
}
