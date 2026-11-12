"use client";

import { ShieldAlert } from "lucide-react";
import { useMemo, useState } from "react";
import { PositionsTable } from "@/components/trade/tables";
import { Card, cx, Empty, LinkButton, PageHeader, Segmented, SimBadge, Skeleton, Stat } from "@/components/ui";
import { useApi } from "@/lib/api";
import { inr, tone } from "@/lib/format";
import type { PositionRow, Summary } from "@/lib/types";

export default function PositionsPage() {
  const { data } = useApi<{ positions: PositionRow[] }>("/api/portfolio/positions?include_closed=true", { refreshInterval: 5000 });
  const { data: s } = useApi<Summary>("/api/portfolio/summary", { refreshInterval: 8000 });
  const [tab, setTab] = useState<"open" | "closed">("open");
  const [cls, setCls] = useState<"all" | "option" | "forex" | "stock" | "crypto">("all");
  const rows = useMemo(() => (data?.positions ?? []).filter((p) => (tab === "open" ? p.quantity !== 0 : p.quantity === 0) && (cls === "all" || p.asset_class === cls)), [data, tab, cls]);
  const open = (data?.positions ?? []).filter((p) => p.quantity !== 0);
  const mtm = open.reduce((a, p) => a + p.unrealized_pnl, 0);
  const shortCalls = open.filter((p) => p.asset_class === "option" && p.kind === "CE" && p.quantity < 0);

  return (
    <div className="space-y-5">
      <PageHeader title="Positions" subtitle="Live mark-to-market on every open position. Close any position with a simulated market order." actions={<SimBadge />} />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Card className="!rounded-xl">{s ? <Stat label="Open positions" value={open.length} /> : <Skeleton className="h-12" />}</Card>
        <Card className="!rounded-xl">{s ? <Stat label="Total M2M (unrealised)" value={inr(mtm, { sign: true })} valueClass={tone(mtm)} /> : <Skeleton className="h-12" />}</Card>
        <Card className="!rounded-xl">{s ? <Stat label="Margin blocked" value={inr(s.margin_blocked)} sub={`Live requirement ${inr(s.margin_live)}`} /> : <Skeleton className="h-12" />}</Card>
        <Card className="!rounded-xl">
          {s ? <Stat label="Margin utilisation" value={`${s.margin_utilisation_pct.toFixed(1)}%`} sub={<span className={cx("capitalize", s.risk_level === "normal" ? "text-up" : "text-warn")}>Risk: {s.risk_level}</span>} /> : <Skeleton className="h-12" />}
        </Card>
      </div>
      {shortCalls.length > 0 && (
        <div className="flex gap-3 rounded-xl border border-down/30 bg-down-soft px-4 py-3 text-[13px] text-[#FFC2C4]">
          <ShieldAlert size={16} className="mt-0.5 shrink-0" />
          <span>You have {shortCalls.length} uncovered short call position(s). Losses on short calls are theoretically unlimited if the underlying rises.</span>
        </div>
      )}
      <Card
        action={<Segmented size="sm" value={cls} onChange={setCls} options={[{ value: "all", label: "All" }, { value: "option", label: "Options" }, { value: "forex", label: "Forex" }, { value: "stock", label: "Stocks" }, { value: "crypto", label: "Crypto" }]} />}
        title={<Segmented size="sm" value={tab} onChange={setTab} options={[{ value: "open", label: "Open" }, { value: "closed", label: "Closed" }]} />}
      >
        {!data ? <Skeleton className="h-60" /> : rows.length ? (
          tab === "open" ? <PositionsTable rows={rows} /> : <ClosedList rows={rows} />
        ) : (
          <Empty title={tab === "open" ? "No open positions" : "No closed positions"} action={<LinkButton href="/options" size="sm" variant="primary">Go to option chain</LinkButton>} />
        )}
      </Card>
    </div>
  );
}

function ClosedList({ rows }: { rows: PositionRow[] }) {
  return (
    <ul className="divide-y divide-ink-750">
      {rows.map((p) => (
        <li key={p.id} className="flex items-center justify-between py-3 text-sm">
          <div>
            <div className="font-medium text-fg">{p.name}</div>
            <div className="text-xs text-fg-muted">{p.symbol}</div>
          </div>
          <div className="text-right">
            <div className={cx("num font-semibold", tone(p.realized_pnl))}>{inr(p.realized_pnl, { sign: true })}</div>
            <div className="text-xs text-fg-muted">Realised</div>
          </div>
        </li>
      ))}
    </ul>
  );
}
