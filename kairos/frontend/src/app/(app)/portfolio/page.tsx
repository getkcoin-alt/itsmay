"use client";

import { useMemo, useState } from "react";
import { AllocationDonut, EquityChart } from "@/components/portfolio/charts";
import { HoldingsTable } from "@/components/trade/tables";
import { Card, Empty, LinkButton, PageHeader, Segmented, SimBadge, Skeleton, Stat } from "@/components/ui";
import { useApi } from "@/lib/api";
import { ASSET_COLOR, ASSET_LABEL, inr, inrCompact, pct, tone } from "@/lib/format";
import type { PositionRow, Summary } from "@/lib/types";

export default function PortfolioPage() {
  const { data: s } = useApi<Summary>("/api/portfolio/summary", { refreshInterval: 8000 });
  const { data: pos } = useApi<{ positions: PositionRow[] }>("/api/portfolio/positions", { refreshInterval: 8000 });
  const [cls, setCls] = useState<"all" | "stock" | "crypto">("all");
  const holdings = useMemo(() => (pos?.positions ?? []).filter((p) => (p.asset_class === "stock" || p.asset_class === "crypto") && (cls === "all" || p.asset_class === cls)), [pos, cls]);
  const bySymbol = useMemo(() => {
    const rows = (pos?.positions ?? []).filter((p) => p.asset_class === "stock" || p.asset_class === "crypto");
    const total = rows.reduce((a, b) => a + b.market_value, 0) || 1;
    return rows.sort((a, b) => b.market_value - a.market_value).slice(0, 8).map((r) => ({ ...r, share: (r.market_value / total) * 100 }));
  }, [pos]);
  const sectors = useMemo(() => {
    const m = new Map<string, number>();
    (pos?.positions ?? []).filter((p) => p.asset_class === "stock").forEach((p) => m.set(p.sector ?? "Other", (m.get(p.sector ?? "Other") ?? 0) + p.market_value));
    const total = Array.from(m.values()).reduce((a, b) => a + b, 0) || 1;
    return Array.from(m.entries()).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ k, v, share: (v / total) * 100 }));
  }, [pos]);

  return (
    <div className="space-y-5">
      <PageHeader title="Portfolio" subtitle="Holdings, allocation and performance across every asset class — valued at simulated prices." actions={<SimBadge />} />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 2xl:grid-cols-6">
        {[
          ["Total value", s && inr(s.total_value), undefined],
          ["Invested", s && inr(s.invested_value), undefined],
          ["Holdings value", s && inr(s.holdings_value), undefined],
          ["Available cash", s && inr(s.available_cash), undefined],
          ["Unrealised P&L", s && inr(s.unrealized_pnl, { sign: true }), s && tone(s.unrealized_pnl)],
          ["Realised P&L", s && inr(s.realized_pnl, { sign: true }), s && tone(s.realized_pnl)],
        ].map(([k, v, c]) => (
          <Card key={k as string} className="!rounded-xl">
            {s ? <Stat label={k} value={v} valueClass={(c as string) ?? undefined} /> : <Skeleton className="h-12" />}
          </Card>
        ))}
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_400px]">
        <Card title="Performance over time" subtitle="Reconstructed by replaying your simulated ledger and fills against historical demo prices">
          <EquityChart height={280} />
          {s && (
            <div className="mt-4 grid grid-cols-2 gap-3 border-t border-ink-700 pt-4 text-sm sm:grid-cols-4">
              <MiniKV k="Net deposits" v={inr(s.net_deposits)} />
              <MiniKV k="Overall P&L" v={`${inr(s.overall_pnl, { sign: true })} (${pct(s.overall_pnl_pct)})`} cls={tone(s.overall_pnl)} />
              <MiniKV k="Today" v={`${inr(s.day_pnl, { sign: true })} (${pct(s.day_pnl_pct)})`} cls={tone(s.day_pnl)} />
              <MiniKV k="Fees paid (sim.)" v={inr(s.fees_paid)} />
            </div>
          )}
        </Card>
        <Card title="Asset allocation">
          {s ? <AllocationDonut data={s.allocation} total={s.total_value} /> : <Skeleton className="h-44" />}
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Largest holdings">
          {!pos ? <Skeleton className="h-40" /> : (
            <ul className="space-y-3">
              {bySymbol.map((r) => (
                <li key={r.symbol}>
                  <div className="mb-1 flex justify-between text-sm"><span className="font-medium text-fg">{r.symbol}</span><span className="num text-fg-muted">{inrCompact(r.market_value)} · {r.share.toFixed(1)}%</span></div>
                  <div className="h-1.5 rounded-full bg-ink-700"><div className="h-full rounded-full" style={{ width: `${r.share}%`, background: ASSET_COLOR[r.asset_class] }} /></div>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Equity sector exposure">
          {!pos ? <Skeleton className="h-40" /> : sectors.length === 0 ? <Empty title="No stock holdings" /> : (
            <ul className="space-y-3">
              {sectors.map((r) => (
                <li key={r.k}>
                  <div className="mb-1 flex justify-between text-sm"><span className="text-fg-soft">{r.k}</span><span className="num text-fg-muted">{r.share.toFixed(1)}%</span></div>
                  <div className="h-1.5 rounded-full bg-ink-700"><div className="h-full rounded-full bg-brand" style={{ width: `${r.share}%` }} /></div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card title="Holdings" subtitle="Stocks (delivery) and crypto (spot)" action={<Segmented size="sm" value={cls} onChange={setCls} options={[{ value: "all", label: "All" }, { value: "stock", label: ASSET_LABEL.stock }, { value: "crypto", label: ASSET_LABEL.crypto }]} />}>
        {!pos ? <Skeleton className="h-60" /> : holdings.length ? <HoldingsTable rows={holdings} /> : <Empty title="No holdings" body="Buy a stock or crypto asset to see it here." action={<LinkButton href="/stocks" size="sm" variant="primary">Explore stocks</LinkButton>} />}
      </Card>
    </div>
  );
}

function MiniKV({ k, v, cls }: { k: string; v: string; cls?: string }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-[0.08em] text-fg-muted">{k}</div>
      <div className={`num mt-0.5 font-semibold ${cls ?? "text-fg"}`}>{v}</div>
    </div>
  );
}
