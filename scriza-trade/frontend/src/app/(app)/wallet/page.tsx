"use client";

import { ArrowDownLeft, ArrowUpRight, Landmark, ShieldCheck } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { Button, Card, cx, Empty, ErrorNote, Input, Modal, PageHeader, SimBadge, Skeleton, Stat, Table, Td, Th } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api, refreshAll, useApi } from "@/lib/api";
import { dateTime, inr } from "@/lib/format";
import type { LedgerEntry } from "@/lib/types";

interface WalletInfo {
  cash_balance: number;
  available_cash: number;
  margin_blocked: number;
  reserved_for_orders: number;
  withdrawable: number;
  total_deposits: number;
  total_withdrawals: number;
  methods: { id: string; label: string }[];
  notice: string;
}

export default function WalletPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-2xl" />}>
      <WalletInner />
    </Suspense>
  );
}

function WalletInner() {
  const { data: w, mutate } = useApi<WalletInfo>("/api/wallet", { refreshInterval: 10000 });
  const { data: tx } = useApi<{ transactions: LedgerEntry[] }>("/api/transactions?type=DEPOSIT,WITHDRAWAL");
  const params = useSearchParams();
  const [mode, setMode] = useState<"deposit" | "withdraw" | null>(null);
  useEffect(() => {
    const a = params.get("action");
    if (a === "deposit" || a === "withdraw") setMode(a);
  }, [params]);

  return (
    <div className="space-y-5">
      <PageHeader
        title="Wallet"
        subtitle="Manage your demo cash. Every movement here is simulated — no bank, card, UPI or crypto rail is connected."
        actions={
          <>
            <SimBadge />
            <Button variant="secondary" onClick={() => setMode("withdraw")}><ArrowUpRight size={16} /> Withdraw</Button>
            <Button variant="primary" onClick={() => setMode("deposit")}><ArrowDownLeft size={16} /> Add demo funds</Button>
          </>
        }
      />
      <div className="grid gap-5 lg:grid-cols-[1.2fr_1fr]">
        <Card>
          {!w ? <Skeleton className="h-36" /> : (
            <div>
              <div className="text-[11px] font-medium uppercase tracking-[0.1em] text-fg-muted">Cash balance (demo INR)</div>
              <div className="num mt-1 text-[32px] font-semibold tracking-tight text-fg">{inr(w.cash_balance)}</div>
              <div className="mt-5 grid grid-cols-2 gap-4 sm:grid-cols-4">
                <Stat label="Available" value={inr(w.available_cash)} valueClass="!text-base" />
                <Stat label="Margin blocked" value={inr(w.margin_blocked)} valueClass="!text-base" />
                <Stat label="Reserved (orders)" value={inr(w.reserved_for_orders)} valueClass="!text-base" />
                <Stat label="Withdrawable" value={inr(w.withdrawable)} valueClass="!text-base" />
              </div>
            </div>
          )}
        </Card>
        <Card>
          {!w ? <Skeleton className="h-36" /> : (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <Stat label="Total deposited" value={inr(w.total_deposits)} valueClass="!text-base text-up" />
                <Stat label="Total withdrawn" value={inr(w.total_withdrawals)} valueClass="!text-base" />
              </div>
              <div className="flex gap-2.5 rounded-lg border border-up/25 bg-up-soft p-3 text-[12.5px] text-fg-soft">
                <ShieldCheck size={16} className="mt-0.5 shrink-0 text-up" />
                {w.notice}
              </div>
            </div>
          )}
        </Card>
      </div>
      <Card title="Deposit & withdrawal history">
        {!tx ? <Skeleton className="h-40" /> : tx.transactions.length === 0 ? <Empty title="No funding activity yet" /> : (
          <Table>
            <thead><tr><Th>Date</Th><Th>Type</Th><Th className="hidden sm:table-cell">Method</Th><Th className="hidden md:table-cell">Reference</Th><Th align="right">Amount</Th><Th align="right" className="hidden sm:table-cell">Balance after</Th></tr></thead>
            <tbody>
              {tx.transactions.map((t) => (
                <tr key={t.id} className="hover:bg-ink-800/60">
                  <Td className="text-fg-muted">{dateTime(t.created_at)}</Td>
                  <Td><span className={cx("inline-flex items-center gap-1.5 font-medium", t.amount > 0 ? "text-up" : "text-fg")}>{t.amount > 0 ? <ArrowDownLeft size={14} /> : <ArrowUpRight size={14} />}{t.type === "DEPOSIT" ? "Deposit" : "Withdrawal"}</span></Td>
                  <Td className="hidden text-fg-soft sm:table-cell">{t.method}</Td>
                  <Td className="hidden font-mono text-xs text-fg-muted md:table-cell">{t.reference}</Td>
                  <Td align="right" className={t.amount > 0 ? "text-up" : "text-fg"}>{inr(t.amount, { sign: true })}</Td>
                  <Td align="right" className="hidden text-fg-muted sm:table-cell">{inr(t.balance_after)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
      <FundsModal mode={mode} info={w} onClose={() => setMode(null)} onDone={() => { mutate(); refreshAll(); }} />
    </div>
  );
}

function FundsModal({ mode, info, onClose, onDone }: { mode: "deposit" | "withdraw" | null; info?: WalletInfo; onClose: () => void; onDone: () => void }) {
  const [amount, setAmount] = useState("50000");
  const [method, setMethod] = useState("SIM_UPI");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const toast = useToast();
  useEffect(() => { setErr(null); setAmount(mode === "withdraw" ? "10000" : "50000"); }, [mode]);
  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await api<{ reference: string; amount: number; balance: number }>(`/api/wallet/${mode}`, { method: "POST", json: mode === "deposit" ? { amount, method } : { amount } });
      toast({ kind: "success", title: mode === "deposit" ? "Demo funds added" : "Demo withdrawal completed", body: `${inr(r.amount)} · ref ${r.reference} · new balance ${inr(r.balance)}` });
      onDone();
      onClose();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      open={mode !== null}
      onClose={onClose}
      title={mode === "deposit" ? "Add demo funds" : "Withdraw demo funds"}
      footer={<Button variant="primary" className="w-full" size="lg" loading={busy} onClick={submit}>{mode === "deposit" ? "Add" : "Withdraw"} {Number(amount) > 0 ? inr(Number(amount)) : ""}</Button>}
    >
      <div className="space-y-4">
        <Input label="Amount (₹)" type="number" inputMode="decimal" min={1} value={amount} onChange={(e) => setAmount(e.target.value)} hint={mode === "withdraw" && info ? `Withdrawable: ${inr(info.withdrawable)}` : "Maximum ₹25,00,000 per demo deposit"} />
        <div className="flex flex-wrap gap-2">
          {(mode === "deposit" ? [10000, 50000, 100000, 500000] : [5000, 10000, 25000]).map((v) => (
            <button key={v} onClick={() => setAmount(String(v))} className="rounded-md border border-ink-600 px-2.5 py-1 text-xs text-fg-soft hover:border-ink-500 hover:text-fg">+{inr(v, { decimals: 0 })}</button>
          ))}
        </div>
        {mode === "deposit" && info && (
          <div className="space-y-2">
            <span className="block text-xs font-medium text-fg-soft">Simulated funding method</span>
            {info.methods.map((m) => (
              <label key={m.id} className={cx("flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2.5 text-sm", method === m.id ? "border-brand/50 bg-brand-soft text-fg" : "border-ink-700 text-fg-soft")}>
                <input type="radio" name="method" className="accent-[#8C9BFF]" checked={method === m.id} onChange={() => setMethod(m.id)} />
                <Landmark size={15} className="text-fg-muted" /> {m.label}
              </label>
            ))}
          </div>
        )}
        {mode === "withdraw" && <p className="text-xs text-fg-muted">Funds are returned to a simulated bank account. No bank details are collected or stored.</p>}
        <ErrorNote error={err} />
      </div>
    </Modal>
  );
}
