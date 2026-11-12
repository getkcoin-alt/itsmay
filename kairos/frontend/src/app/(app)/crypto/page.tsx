"use client";

import Link from "next/link";
import { InstrumentTable, Sparkline } from "@/components/market/instrument-list";
import { AssetIcon, Card, Change, Flash, PageHeader, SimBadge, Skeleton } from "@/components/ui";
import { useApi } from "@/lib/api";
import { price } from "@/lib/format";
import { useLiveQuotes } from "@/lib/live";
import type { Quote } from "@/lib/types";

const FEATURED = ["BTC", "ETH", "BNB", "SOL", "XRP"];

export default function CryptoPage() {
  const { data } = useApi<{ instruments: Quote[] }>("/api/market/instruments?asset_class=crypto", { refreshInterval: 5000 });
  const { ticks } = useLiveQuotes(FEATURED);
  const featured = FEATURED.map((s) => data?.instruments.find((i) => i.symbol === s)).filter(Boolean) as Quote[];
  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Digital assets" title="Crypto" subtitle="Simulated INR spot market. No wallets, keys, custody or blockchain transfers are involved." actions={<SimBadge />} />
      <div className="-mx-4 flex snap-x gap-3 overflow-x-auto px-4 pb-1 sm:mx-0 sm:grid sm:grid-cols-3 sm:px-0 xl:grid-cols-5">
        {!data && Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-[118px] min-w-[200px] rounded-2xl" />)}
        {featured.map((q0) => {
          const q = { ...q0, ...(ticks[q0.symbol] ?? {}) };
          return (
            <Link key={q.symbol} href={`/crypto/${q.symbol}`} className="min-w-[200px] snap-start rounded-2xl border border-ink-700/80 bg-ink-850 p-4 transition-colors hover:border-ink-600">
              <div className="flex items-center gap-2.5">
                <AssetIcon symbol={q.symbol} assetClass="crypto" size={30} />
                <div>
                  <div className="text-sm font-semibold text-fg">{q.symbol}</div>
                  <div className="text-[11px] text-fg-muted">{q.name}</div>
                </div>
              </div>
              <div className="mt-3 flex items-end justify-between">
                <div>
                  <div className="num text-[15px] font-semibold text-fg"><Flash value={q.ltp}>{price(q.ltp, q.decimals, "INR")}</Flash></div>
                  <Change percent={q.change_pct} showAbs={false} className="text-xs" />
                </div>
                <Sparkline symbol={q.symbol} positive={q.change >= 0} width={70} height={30} />
              </div>
            </Link>
          );
        })}
      </div>
      <Card>
        <InstrumentTable assetClass="crypto" title="crypto" />
      </Card>
    </div>
  );
}
