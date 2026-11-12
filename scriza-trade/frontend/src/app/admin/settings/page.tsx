"use client";

import { useEffect, useState } from "react";
import { Button, Card, Input, PageHeader, Skeleton, Toggle } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api, useApi } from "@/lib/api";
import { ASSET_LABEL } from "@/lib/format";

type Fee = { pct: number; cap: number; flat: number };
interface Doc {
  fees: Record<string, Fee>;
  trading: { forex_leverage: number; max_order_value_inr: number; require_kyc_for_trading: boolean; forex_inr_pairs_only: boolean; options_selling_enabled: boolean; trading_halted: boolean };
  wallet: { max_single_deposit: number; max_balance: number; initial_demo_balance: number };
}

export default function AdminSettings() {
  const { data, mutate } = useApi<{ settings: Doc }>("/api/admin/settings");
  const [doc, setDoc] = useState<Doc | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  useEffect(() => { if (data) setDoc(structuredClone(data.settings)); }, [data]);
  if (!doc) return <Skeleton className="h-[600px] rounded-2xl" />;

  const save = async () => {
    setBusy(true);
    try {
      await api("/api/admin/settings", { method: "PUT", json: doc });
      toast({ kind: "success", title: "Platform settings saved", body: "Changes apply to new orders immediately and are audit-logged." });
      mutate();
    } catch (e) {
      toast({ kind: "error", title: "Could not save", body: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };
  const setFee = (cls: string, k: keyof Fee, v: string) => setDoc({ ...doc, fees: { ...doc.fees, [cls]: { ...doc.fees[cls], [k]: Number(v) } } });
  const setT = <K extends keyof Doc["trading"]>(k: K, v: Doc["trading"][K]) => setDoc({ ...doc, trading: { ...doc.trading, [k]: v } });

  return (
    <div className="space-y-5">
      <PageHeader title="Platform settings" subtitle="Demonstration fee schedule, risk controls and compliance toggles." actions={<Button variant="primary" loading={busy} onClick={save}>Save changes</Button>} />
      <Card title="Simulated fee schedule" subtitle="Per order: min(notional × rate, cap) + flat fee. Cap 0 = no cap.">
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {Object.entries(doc.fees).map(([cls, f]) => (
            <div key={cls} className="space-y-3 rounded-xl border border-ink-700 bg-ink-900/50 p-4">
              <div className="text-sm font-semibold text-fg">{ASSET_LABEL[cls] ?? cls}</div>
              <Input label="Rate (%)" type="number" step="0.001" value={+(f.pct * 100).toFixed(4)} onChange={(e) => setFee(cls, "pct", String(Number(e.target.value) / 100))} />
              <Input label="Cap (₹)" type="number" value={f.cap} onChange={(e) => setFee(cls, "cap", e.target.value)} />
              <Input label="Flat (₹)" type="number" value={f.flat} onChange={(e) => setFee(cls, "flat", e.target.value)} />
            </div>
          ))}
        </div>
      </Card>
      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Trading controls">
          <div className="space-y-4">
            <Row label="Halt all trading" sub="Every new order is rejected while enabled." on={doc.trading.trading_halted} set={(v) => setT("trading_halted", v)} danger />
            <Row label="Allow option writing" sub="Disable to block opening short option positions." on={doc.trading.options_selling_enabled} set={(v) => setT("options_selling_enabled", v)} />
            <Row label="Require approved KYC to trade" sub="Users must pass simulated KYC review first." on={doc.trading.require_kyc_for_trading} set={(v) => setT("require_kyc_for_trading", v)} />
            <Row label="Forex: INR pairs only" sub="Jurisdiction mode restricting cross-currency pairs." on={doc.trading.forex_inr_pairs_only} set={(v) => setT("forex_inr_pairs_only", v)} />
            <div className="grid grid-cols-2 gap-3 pt-2">
              <Input label="Forex leverage (1:x)" type="number" value={doc.trading.forex_leverage} onChange={(e) => setT("forex_leverage", Number(e.target.value))} />
              <Input label="Max order value (₹)" type="number" value={doc.trading.max_order_value_inr} onChange={(e) => setT("max_order_value_inr", Number(e.target.value))} />
            </div>
          </div>
        </Card>
        <Card title="Demo wallet limits">
          <div className="grid gap-3 sm:grid-cols-2">
            <Input label="Max single deposit (₹)" type="number" value={doc.wallet.max_single_deposit} onChange={(e) => setDoc({ ...doc, wallet: { ...doc.wallet, max_single_deposit: Number(e.target.value) } })} />
            <Input label="Max cash balance (₹)" type="number" value={doc.wallet.max_balance} onChange={(e) => setDoc({ ...doc, wallet: { ...doc.wallet, max_balance: Number(e.target.value) } })} />
            <Input label="Initial demo balance (₹)" type="number" value={doc.wallet.initial_demo_balance} onChange={(e) => setDoc({ ...doc, wallet: { ...doc.wallet, initial_demo_balance: Number(e.target.value) } })} hint="Applied to new and reset accounts" />
          </div>
        </Card>
      </div>
    </div>
  );
}

function Row({ label, sub, on, set, danger }: { label: string; sub: string; on: boolean; set: (v: boolean) => void; danger?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div>
        <div className={`text-sm font-medium ${danger && on ? "text-down" : "text-fg"}`}>{label}</div>
        <div className="text-xs text-fg-muted">{sub}</div>
      </div>
      <Toggle checked={on} onChange={set} label={label} />
    </div>
  );
}
