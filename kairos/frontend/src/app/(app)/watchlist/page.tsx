"use client";

import { Star, Trash2 } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { useWatchlist } from "@/components/market/instrument-list";
import { AssetIcon, Button, Card, Change, Empty, Flash, LinkButton, PageHeader, Segmented, SimBadge, Skeleton, Table, Td, Th } from "@/components/ui";
import { ASSET_LABEL, assetHref, compact, price } from "@/lib/format";
import { useLiveQuotes } from "@/lib/live";

export default function WatchlistPage() {
  const w = useWatchlist();
  const [filter, setFilter] = useState<"all" | "stock" | "crypto" | "forex" | "index">("all");
  const { ticks } = useLiveQuotes((w.items ?? []).map((i) => i.symbol));
  const rows = useMemo(() => (w.items ?? []).map((i) => ({ ...i, ...(ticks[i.symbol] ?? {}) })).filter((i) => filter === "all" || i.asset_class === filter), [w.items, ticks, filter]);
  return (
    <div className="space-y-5">
      <PageHeader title="Watchlist" subtitle="Instruments you are tracking, updated with simulated prices." actions={<SimBadge />} />
      <Card>
        <div className="mb-4">
          <Segmented size="sm" value={filter} onChange={setFilter} options={[{ value: "all", label: "All" }, { value: "stock", label: "Stocks" }, { value: "crypto", label: "Crypto" }, { value: "forex", label: "Forex" }, { value: "index", label: "Indices" }]} />
        </div>
        {!w.items ? (
          <Skeleton className="h-60" />
        ) : rows.length === 0 ? (
          <Empty icon={<Star size={22} />} title="Nothing here yet" body="Use the star on any instrument to add it to your watchlist." action={<LinkButton href="/markets" size="sm" variant="primary">Browse markets</LinkButton>} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Instrument</Th>
                <Th align="right">Last (sim.)</Th>
                <Th align="right">Change</Th>
                <Th align="right" className="hidden sm:table-cell">Day range</Th>
                <Th align="right" className="hidden sm:table-cell">Volume</Th>
                <Th align="right" />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.symbol} className="hover:bg-ink-800/60">
                  <Td>
                    <Link href={assetHref(r.symbol, r.asset_class)} className="flex items-center gap-3 hover:text-brand">
                      <AssetIcon symbol={r.symbol} assetClass={r.asset_class} size={30} />
                      <div>
                        <div className="font-semibold text-fg">{r.symbol}</div>
                        <div className="text-xs text-fg-muted">{ASSET_LABEL[r.asset_class]} · <span className="hidden sm:inline">{r.name}</span></div>
                      </div>
                    </Link>
                  </Td>
                  <Td align="right" className="text-fg"><Flash value={r.ltp}>{price(r.ltp, r.decimals, r.currency)}</Flash></Td>
                  <Td align="right"><Change value={r.change} percent={r.change_pct} decimals={r.decimals} showAbs={false} /></Td>
                  <Td align="right" className="hidden text-fg-muted sm:table-cell">{price(r.low, r.decimals)} – {price(r.high, r.decimals)}</Td>
                  <Td align="right" className="hidden text-fg-muted sm:table-cell">{compact(r.volume)}</Td>
                  <Td align="right">
                    <Button size="sm" variant="ghost" onClick={() => w.toggle(r.symbol)} aria-label={`Remove ${r.symbol}`}><Trash2 size={15} /></Button>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
