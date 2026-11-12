"use client";

import { Star } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { api, useApi } from "@/lib/api";
import { assetHref, compact, price } from "@/lib/format";
import { useLiveQuotes } from "@/lib/live";
import type { Quote } from "@/lib/types";
import { useToast } from "../ui/toast";
import { AssetIcon, Badge, Change, cx, Flash, Input, Skeleton, Table, Td, Th } from "../ui";

export function useWatchlist() {
  const { data, mutate } = useApi<{ items: Quote[] }>("/api/watchlist");
  const set = useMemo(() => new Set((data?.items ?? []).map((i) => i.symbol)), [data]);
  const toast = useToast();
  const toggle = async (symbol: string) => {
    try {
      if (set.has(symbol)) {
        await api(`/api/watchlist/${symbol}`, { method: "DELETE" });
        toast({ kind: "info", title: `${symbol} removed from watchlist` });
      } else {
        await api("/api/watchlist", { method: "POST", json: { symbol } });
        toast({ kind: "success", title: `${symbol} added to watchlist` });
      }
      mutate();
    } catch (e) {
      toast({ kind: "error", title: "Watchlist update failed", body: (e as Error).message });
    }
  };
  return { items: data?.items, has: (s: string) => set.has(s), toggle };
}

export function WatchStar({ symbol, className }: { symbol: string; className?: string }) {
  const w = useWatchlist();
  const on = w.has(symbol);
  return (
    <button
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        w.toggle(symbol);
      }}
      aria-label={on ? `Remove ${symbol} from watchlist` : `Add ${symbol} to watchlist`}
      aria-pressed={on}
      className={cx("rounded-md p-1.5 transition-colors hover:bg-ink-700", on ? "text-warn" : "text-fg-faint hover:text-fg-soft", className)}
    >
      <Star size={16} fill={on ? "currentColor" : "none"} />
    </button>
  );
}

export function Sparkline({ symbol, positive, width = 88, height = 28 }: { symbol: string; positive: boolean; width?: number; height?: number }) {
  const { data } = useApi<{ candles: { close: number }[] }>(`/api/market/candles/${encodeURIComponent(symbol)}?range=1D`, { refreshInterval: 60000, revalidateOnFocus: false });
  const pts = data?.candles.map((c) => c.close) ?? [];
  if (pts.length < 2) return <div style={{ width, height }} />;
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const d = pts
    .map((p, i) => `${i === 0 ? "M" : "L"}${((i / (pts.length - 1)) * width).toFixed(1)},${(height - ((p - min) / (max - min || 1)) * (height - 4) - 2).toFixed(1)}`)
    .join(" ");
  const c = positive ? "#22C993" : "#F2595F";
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden>
      <path d={d} fill="none" stroke={c} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

export function InstrumentTable({ assetClass, title, search = true, compactRows = false, showSpark = true }: { assetClass?: string; title?: string; search?: boolean; compactRows?: boolean; showSpark?: boolean }) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<"default" | "change" | "volume">("default");
  const { data, isLoading } = useApi<{ instruments: Quote[] }>(`/api/market/instruments${assetClass ? `?asset_class=${assetClass}` : ""}`, { refreshInterval: 5000 });
  const { ticks } = useLiveQuotes((data?.instruments ?? []).map((i) => i.symbol));
  const rows = useMemo(() => {
    let list = (data?.instruments ?? []).map((i) => (ticks[i.symbol] ? { ...i, ...ticks[i.symbol] } : i));
    const n = q.trim().toLowerCase();
    if (n) list = list.filter((i) => i.symbol.toLowerCase().includes(n) || i.name.toLowerCase().includes(n) || (i.sector ?? "").toLowerCase().includes(n));
    if (sort === "change") list = [...list].sort((a, b) => b.change_pct - a.change_pct);
    if (sort === "volume") list = [...list].sort((a, b) => b.volume * b.ltp - a.volume * a.ltp);
    return list;
  }, [data, ticks, q, sort]);

  return (
    <div>
      {(search || title) && (
        <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          {search && (
            <div className="sm:w-72">
              <Input placeholder={`Filter ${title ?? "instruments"}…`} value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filter instruments" />
            </div>
          )}
          <div className="flex gap-1 text-xs">
            {(["default", "change", "volume"] as const).map((s) => (
              <button key={s} onClick={() => setSort(s)} className={cx("rounded-md px-2.5 py-1.5 font-medium", sort === s ? "bg-ink-700 text-fg" : "text-fg-muted hover:text-fg")}>
                {s === "default" ? "Default" : s === "change" ? "Top movers" : "Most active"}
              </button>
            ))}
          </div>
        </div>
      )}
      {isLoading && !data ? (
        <div className="space-y-2">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-12" />)}</div>
      ) : (
        <>
          {/* Mobile cards */}
          <ul className="divide-y divide-ink-750 md:hidden">
            {rows.map((r) => (
              <li key={r.symbol}>
                <Link href={assetHref(r.symbol, r.asset_class)} className="flex items-center gap-3 py-3">
                  <AssetIcon symbol={r.symbol} assetClass={r.asset_class} />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-semibold text-fg">{r.symbol}</div>
                    <div className="truncate text-xs text-fg-muted">{r.name}</div>
                  </div>
                  {showSpark && <Sparkline symbol={r.symbol} positive={r.change >= 0} width={60} height={24} />}
                  <div className="w-24 text-right">
                    <div className="num text-sm font-medium text-fg">
                      <Flash value={r.ltp}>{price(r.ltp, r.decimals, r.currency)}</Flash>
                    </div>
                    <Change percent={r.change_pct} showAbs={false} className="text-xs" />
                  </div>
                </Link>
              </li>
            ))}
          </ul>
          {/* Desktop table */}
          <div className="hidden md:block">
            <Table>
              <thead>
                <tr>
                  <Th>Instrument</Th>
                  <Th align="right">Last (sim.)</Th>
                  <Th align="right">Change</Th>
                  {!compactRows && <Th align="right">Day range</Th>}
                  {!compactRows && <Th align="right">Volume</Th>}
                  {showSpark && <Th align="right">Today</Th>}
                  <Th align="right" />
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.symbol} className="group transition-colors hover:bg-ink-800/70">
                    <Td>
                      <Link href={assetHref(r.symbol, r.asset_class)} className="flex items-center gap-3">
                        <AssetIcon symbol={r.symbol} assetClass={r.asset_class} />
                        <div className="min-w-0">
                          <div className="flex items-center gap-2 font-semibold text-fg group-hover:text-brand">
                            {r.symbol}
                            {!r.tradable && r.asset_class !== "index" && <Badge tone="warn">{r.restriction ? "Restricted" : "Disabled"}</Badge>}
                          </div>
                          <div className="max-w-[240px] truncate text-xs text-fg-muted">{r.name}{r.sector && r.asset_class === "stock" ? ` · ${r.sector}` : ""}</div>
                        </div>
                      </Link>
                    </Td>
                    <Td align="right" className="font-medium text-fg">
                      <Flash value={r.ltp}>{price(r.ltp, r.decimals, r.currency)}</Flash>
                    </Td>
                    <Td align="right">
                      <Change value={r.change} percent={r.change_pct} decimals={r.decimals} />
                    </Td>
                    {!compactRows && (
                      <Td align="right" className="text-fg-muted">
                        {price(r.low, r.decimals)} – {price(r.high, r.decimals)}
                      </Td>
                    )}
                    {!compactRows && <Td align="right" className="text-fg-muted">{compact(r.volume)}</Td>}
                    {showSpark && (
                      <Td align="right">
                        <span className="inline-block">
                          <Sparkline symbol={r.symbol} positive={r.change >= 0} />
                        </span>
                      </Td>
                    )}
                    <Td align="right">{r.asset_class !== "index" && <WatchStar symbol={r.symbol} />}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </div>
          {rows.length === 0 && <div className="py-8 text-center text-sm text-fg-muted">No instruments match your filter.</div>}
        </>
      )}
    </div>
  );
}
