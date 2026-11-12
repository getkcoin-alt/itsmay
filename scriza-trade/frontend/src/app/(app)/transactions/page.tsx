"use client";

import { Download } from "lucide-react";
import { useState } from "react";
import { Button, Card, cx, Empty, PageHeader, Select, SimBadge, Skeleton, Table, Td, Th } from "@/components/ui";
import { useApi } from "@/lib/api";
import { dateTime, inr } from "@/lib/format";
import type { LedgerEntry } from "@/lib/types";

const TYPES: Record<string, string> = {
  DEPOSIT: "Deposit",
  WITHDRAWAL: "Withdrawal",
  TRADE_BUY: "Buy",
  TRADE_SELL: "Sell",
  OPTION_PREMIUM_PAID: "Option premium paid",
  OPTION_PREMIUM_RECEIVED: "Option premium received",
  FX_PNL: "Forex P&L settled",
  FEE: "Fee",
  EXPIRY_SETTLEMENT: "Expiry settlement",
};

export default function TransactionsPage() {
  const [type, setType] = useState("");
  const { data } = useApi<{ transactions: LedgerEntry[] }>(`/api/transactions?limit=1000${type ? `&type=${type}` : ""}`);
  const exportCsv = () => {
    if (!data) return;
    const rows = [["date", "type", "description", "symbol", "amount_inr", "balance_after_inr", "reference"], ...data.transactions.map((t) => [t.created_at, t.type, t.description, t.symbol ?? "", t.amount.toFixed(2), t.balance_after.toFixed(2), t.reference])];
    const csv = rows.map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\n");
    const url = URL.createObjectURL(new Blob([`# SIMULATED DEMO DATA\n${csv}`], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "scriza-trade-demo-transactions.csv";
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="space-y-5">
      <PageHeader
        title="Transactions"
        subtitle="Complete cash ledger. Your balance always equals the sum of these entries."
        actions={<><SimBadge /><Button variant="secondary" size="sm" onClick={exportCsv} disabled={!data}><Download size={15} /> Export CSV</Button></>}
      />
      <Card>
        <div className="mb-4 sm:w-64">
          <Select value={type} onChange={(e) => setType(e.target.value)} aria-label="Transaction type">
            <option value="">All transaction types</option>
            {Object.entries(TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
        </div>
        {!data ? <Skeleton className="h-96" /> : data.transactions.length === 0 ? <Empty title="No transactions" /> : (
          <Table>
            <thead><tr><Th>Date</Th><Th>Type</Th><Th className="hidden md:table-cell">Description</Th><Th align="right">Amount</Th><Th align="right" className="hidden sm:table-cell">Balance</Th><Th className="hidden lg:table-cell">Reference</Th></tr></thead>
            <tbody>
              {data.transactions.map((t) => (
                <tr key={t.id} className="hover:bg-ink-800/60">
                  <Td className="text-fg-muted">{dateTime(t.created_at)}</Td>
                  <Td>
                    <div className="font-medium text-fg">{TYPES[t.type] ?? t.type}</div>
                    <div className="max-w-[160px] truncate text-xs text-fg-muted md:hidden">{t.symbol ?? t.method}</div>
                  </Td>
                  <Td className="hidden max-w-[360px] truncate text-fg-soft md:table-cell">{t.description}</Td>
                  <Td align="right" className={cx(t.amount > 0 ? "text-up" : "text-fg")}>{inr(t.amount, { sign: true })}</Td>
                  <Td align="right" className="hidden text-fg-muted sm:table-cell">{inr(t.balance_after)}</Td>
                  <Td className="hidden font-mono text-xs text-fg-muted lg:table-cell">{t.reference}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
