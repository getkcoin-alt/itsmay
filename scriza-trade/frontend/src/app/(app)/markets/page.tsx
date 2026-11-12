"use client";

import Link from "next/link";
import { useState } from "react";
import { IndexStrip } from "@/components/market/index-strip";
import { InstrumentTable } from "@/components/market/instrument-list";
import { AssetIcon, Card, cx, PageHeader, Segmented, SimBadge, Skeleton } from "@/components/ui";
import { useApi } from "@/lib/api";
import { assetHref, pct, price, tone } from "@/lib/format";
import type { Quote } from "@/lib/types";

interface Overview {
  indices: Quote[];
  gainers: Quote[];
  losers: Quote[];
  most_active: Quote[];
  crypto_movers: Quote[];
  forex: Quote[];
  breadth: Record<string, { advancers: number; decliners: number }>;
  session: { label: string; server_time: string };
  disclaimer: string;
}

export default function MarketsPage() {
  const { data } = useApi<Overview>("/api/market/overview", { refreshInterval: 6000 });
  const [tab, setTab] = useState<"stock" | "crypto" | "forex" | "index">("stock");
  return (
    <div className="space-y-5">
      <PageHeader title="Markets" subtitle={data ? `${data.session.label} · ${data.disclaimer}` : "Simulated market overview"} actions={<SimBadge />} />
      <IndexStrip indices={data?.indices} />
      <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-4">
        <MoverCard title="Top gainers" rows={data?.gainers} />
        <MoverCard title="Top losers" rows={data?.losers} />
        <MoverCard title="Most active (value)" rows={data?.most_active} />
        <MoverCard title="Crypto movers" rows={data?.crypto_movers} />
      </div>
      {data && (
        <div className="grid gap-3 sm:grid-cols-3">
          {(["stock", "crypto", "forex"] as const).map((k) => {
            const b = data.breadth[k];
            const total = b.advancers + b.decliners || 1;
            return (
              <div key={k} className="rounded-xl border border-ink-700 bg-ink-850 px-4 py-3">
                <div className="flex justify-between text-xs"><span className="font-medium capitalize text-fg-soft">{k === "stock" ? "Equity" : k} breadth</span><span className="text-fg-muted">{b.advancers} ▲ · {b.decliners} ▼</span></div>
                <div className="mt-2 flex h-1.5 overflow-hidden rounded-full bg-ink-700">
                  <div className="bg-up" style={{ width: `${(b.advancers / total) * 100}%` }} />
                  <div className="bg-down" style={{ width: `${(b.decliners / total) * 100}%` }} />
                </div>
              </div>
            );
          })}
        </div>
      )}
      <Card>
        <div className="mb-4">
          <Segmented value={tab} onChange={setTab} options={[{ value: "stock", label: "Stocks" }, { value: "crypto", label: "Crypto" }, { value: "forex", label: "Forex" }, { value: "index", label: "Indices" }]} />
        </div>
        <InstrumentTable key={tab} assetClass={tab} title={tab === "stock" ? "stocks" : tab} />
      </Card>
    </div>
  );
}

function MoverCard({ title, rows }: { title: string; rows?: Quote[] }) {
  return (
    <Card title={title}>
      {!rows ? <Skeleton className="h-36" /> : (
        <ul className="space-y-2.5">
          {rows.map((q) => (
            <li key={q.symbol}>
              <Link href={assetHref(q.symbol, q.asset_class)} className="flex items-center gap-2.5 text-sm hover:text-brand">
                <AssetIcon symbol={q.symbol} assetClass={q.asset_class} size={26} />
                <span className="min-w-0 flex-1 truncate font-medium text-fg-soft">{q.symbol}</span>
                <span className="num text-fg">{price(q.ltp, q.decimals)}</span>
                <span className={cx("num w-16 text-right text-xs font-semibold", tone(q.change_pct))}>{pct(q.change_pct)}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
