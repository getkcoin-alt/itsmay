"use client";

import { useState } from "react";
import { Card, cx, Empty, Input, PageHeader, Select, Skeleton, Table, Td, Th } from "@/components/ui";
import { useApi } from "@/lib/api";
import { dateTime, inr } from "@/lib/format";
import type { LedgerEntry } from "@/lib/types";

export default function AdminTransactions() {
  const [type, setType] = useState("");
  const [q, setQ] = useState("");
  const p = new URLSearchParams();
  if (type) p.set("type", type);
  if (q) p.set("q", q);
  const { data } = useApi<{ transactions: LedgerEntry[] }>(`/api/admin/transactions?${p}`);
  return (
    <div className="space-y-5">
      <PageHeader title="Transactions" subtitle="Platform-wide simulated cash ledger." />
      <Card>
        <div className="mb-4 flex flex-col gap-3 sm:flex-row">
          <div className="sm:w-60">
            <Select value={type} onChange={(e) => setType(e.target.value)} aria-label="Type">
              <option value="">All types</option>
              {["DEPOSIT", "WITHDRAWAL", "TRADE_BUY", "TRADE_SELL", "OPTION_PREMIUM_PAID", "OPTION_PREMIUM_RECEIVED", "FX_PNL", "FEE", "EXPIRY_SETTLEMENT"].map((t) => <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}
            </Select>
          </div>
          <div className="sm:w-72"><Input placeholder="Reference, email or symbol…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search" /></div>
        </div>
        {!data ? <Skeleton className="h-96" /> : data.transactions.length === 0 ? <Empty title="No transactions" /> : (
          <Table>
            <thead><tr><Th>Date</Th><Th>User</Th><Th>Type</Th><Th>Description</Th><Th align="right">Amount</Th><Th align="right">Balance after</Th><Th>Reference</Th></tr></thead>
            <tbody>
              {data.transactions.map((t) => (
                <tr key={t.id} className="hover:bg-ink-800/60">
                  <Td className="text-fg-muted">{dateTime(t.created_at)}</Td>
                  <Td className="text-fg-soft">{t.user_email}</Td>
                  <Td className="text-xs font-semibold uppercase tracking-wide text-fg-soft">{t.type.replace(/_/g, " ")}</Td>
                  <Td className="max-w-[320px] truncate text-fg-soft">{t.description}</Td>
                  <Td align="right" className={cx(t.amount > 0 ? "text-up" : "text-fg")}>{inr(t.amount, { sign: true })}</Td>
                  <Td align="right" className="text-fg-muted">{inr(t.balance_after)}</Td>
                  <Td className="font-mono text-xs text-fg-muted">{t.reference}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
