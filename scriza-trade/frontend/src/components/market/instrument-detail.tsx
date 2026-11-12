"use client";

import { ArrowLeft, Layers } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { useApi } from "@/lib/api";
import { ASSET_LABEL, compact, inr, inrCompact, pct, price, qty, tone } from "@/lib/format";
import { useLiveQuotes } from "@/lib/live";
import type { PositionRow, Quote } from "@/lib/types";
import { OrderTicket } from "../trade/order-ticket";
import { AssetIcon, Badge, Button, Card, Change, cx, Empty, ErrorNote, Flash, Modal, SimBadge, Skeleton } from "../ui";
import { WatchStar } from "./instrument-list";
import { PriceChart } from "./price-chart";

export function InstrumentDetail({ symbol, backHref, backLabel }: { symbol: string; backHref: string; backLabel: string }) {
  const { data, error } = useApi<{ quote: Quote; disclaimer: string }>(`/api/market/quote/${encodeURIComponent(symbol)}`, { refreshInterval: 4000 });
  const { ticks } = useLiveQuotes([symbol]);
  const { data: pos } = useApi<{ positions: PositionRow[] }>("/api/portfolio/positions", { refreshInterval: 10000 });
  const [sheet, setSheet] = useState<"BUY" | "SELL" | null>(null);
  const q = data?.quote ? { ...data.quote, ...(ticks[symbol] ?? {}) } : undefined;
  const position = pos?.positions.find((p) => p.symbol === symbol);

  if (error) return <ErrorNote error={error} />;
  if (!q) return <DetailSkeleton />;

  const inst = { symbol: q.symbol, name: q.name, asset_class: q.asset_class, ltp: q.ltp, decimals: q.decimals, currency: q.currency, lot_size: q.lot_size, min_qty: q.min_qty, tradable: q.tradable };
  const stats: [string, string][] = [
    ["Open", price(q.open, q.decimals)],
    ["Day high", price(q.high, q.decimals)],
    ["Day low", price(q.low, q.decimals)],
    ["Prev. close", price(q.prev_close, q.decimals)],
    ["Bid / Ask", `${price(q.bid, q.decimals)} / ${price(q.ask, q.decimals)}`],
    ["52W high", price(q.week52_high, q.decimals)],
    ["52W low", price(q.week52_low, q.decimals)],
    [q.asset_class === "forex" ? "Volume (units)" : "Volume", compact(q.volume)],
  ];

  return (
    <div className="pb-16 lg:pb-0">
      <Link href={backHref} className="mb-4 inline-flex items-center gap-1.5 text-sm text-fg-muted hover:text-fg">
        <ArrowLeft size={15} /> {backLabel}
      </Link>
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0 space-y-5">
          <Card>
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="flex min-w-0 items-center gap-3">
                <AssetIcon symbol={q.symbol} assetClass={q.asset_class} size={44} />
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h1 className="text-xl font-semibold tracking-tight text-fg">{q.symbol}</h1>
                    <Badge>{ASSET_LABEL[q.asset_class]}</Badge>
                    <Badge>{q.exchange}</Badge>
                    {!q.tradable && <Badge tone="warn">{q.restriction ?? "Trading disabled"}</Badge>}
                  </div>
                  <p className="truncate text-sm text-fg-muted">{q.name}{q.sector && q.asset_class === "stock" ? ` · ${q.sector}` : ""}</p>
                </div>
              </div>
              <div className="flex items-center gap-1">
                <SimBadge />
                <WatchStar symbol={q.symbol} />
              </div>
            </div>
            <div className="mt-4 flex flex-wrap items-end gap-x-4 gap-y-1">
              <div className="num text-3xl font-semibold tracking-tight text-fg sm:text-[34px]">
                <Flash value={q.ltp}>{price(q.ltp, q.decimals, q.currency)}</Flash>
              </div>
              <Change value={q.change} percent={q.change_pct} decimals={q.decimals} className="pb-1 text-[15px] font-medium" />
              <span className="pb-1.5 text-xs text-fg-faint">Today · simulated 24×7 session</span>
            </div>
            <div className="mt-5">
              <PriceChart symbol={q.symbol} decimals={q.decimals} live={q.ltp} />
            </div>
          </Card>

          <div className="grid gap-5 md:grid-cols-2">
            <Card title="Key statistics">
              <dl className="grid grid-cols-2 gap-x-6 gap-y-3">
                {stats.map(([k, v]) => (
                  <div key={k}>
                    <dt className="text-[11px] uppercase tracking-[0.08em] text-fg-muted">{k}</dt>
                    <dd className="num mt-0.5 text-sm text-fg">{v}</dd>
                  </div>
                ))}
              </dl>
              <DayRange low={q.low} high={q.high} ltp={q.ltp} />
            </Card>
            <Card title="Your position">
              {position ? (
                <div className="space-y-3">
                  <div className="flex items-baseline justify-between">
                    <span className="text-sm text-fg-muted">{position.side === "SHORT" ? "Short" : "Holding"}</span>
                    <span className="num text-lg font-semibold text-fg">
                      {qty(Math.abs(position.lots ?? position.quantity))} {position.lots !== undefined ? "lots" : q.asset_class === "crypto" ? q.symbol : "shares"}
                    </span>
                  </div>
                  <Line k="Average price" v={price(position.avg_price, q.decimals, q.currency)} />
                  {q.asset_class !== "forex" && <Line k="Invested" v={inr(position.invested)} />}
                  {q.asset_class !== "forex" && <Line k="Current value" v={inr(position.market_value)} />}
                  {q.asset_class === "forex" && <Line k="Margin blocked" v={inr(position.margin_blocked)} />}
                  <Line k="Unrealised P&L" v={`${inr(position.unrealized_pnl, { sign: true })} (${pct(position.unrealized_pnl_pct)})`} cls={tone(position.unrealized_pnl)} />
                  <Line k="Today" v={inr(position.day_pnl, { sign: true })} cls={tone(position.day_pnl)} />
                </div>
              ) : (
                <Empty title="No open position" body={`Place a simulated order to ${q.asset_class === "forex" ? "open a position" : "start building a holding"}.`} />
              )}
            </Card>
          </div>

          <Card title="About">
            <About q={q} />
          </Card>
        </div>

        <aside className="hidden lg:block">
          <div className="sticky top-20 space-y-4">
            <OrderTicket inst={inst} />
            {q.has_options && (
              <Link href={`/options?underlying=${q.symbol}`} className="flex items-center justify-between rounded-xl border border-ink-700 bg-ink-850 px-4 py-3 text-sm text-fg-soft hover:border-ink-600 hover:text-fg">
                <span className="flex items-center gap-2"><Layers size={16} className="text-brand" /> View {q.symbol} option chain</span>
                <span aria-hidden>→</span>
              </Link>
            )}
          </div>
        </aside>
      </div>

      {/* Mobile sticky trade bar */}
      <div className="fixed inset-x-0 bottom-[60px] z-30 border-t border-ink-700 bg-ink-900/95 px-4 py-3 backdrop-blur lg:hidden">
        <div className="mx-auto flex max-w-lg gap-3">
          <Button variant="buy" size="lg" className="flex-1" disabled={!q.tradable} onClick={() => setSheet("BUY")}>Buy</Button>
          <Button variant="sell" size="lg" className="flex-1" disabled={!q.tradable} onClick={() => setSheet("SELL")}>Sell</Button>
        </div>
      </div>
      <Modal open={sheet !== null} onClose={() => setSheet(null)} title={`${q.symbol} · ${price(q.ltp, q.decimals, q.currency)}`}>
        {sheet && <OrderTicket inst={inst} defaultSide={sheet} compact />}
      </Modal>
    </div>
  );
}

function Line({ k, v, cls }: { k: string; v: string; cls?: string }) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-fg-muted">{k}</span>
      <span className={cx("num text-fg", cls)}>{v}</span>
    </div>
  );
}

function DayRange({ low, high, ltp }: { low: number; high: number; ltp: number }) {
  const p = high > low ? ((ltp - low) / (high - low)) * 100 : 50;
  return (
    <div className="mt-5">
      <div className="mb-1.5 flex justify-between text-[11px] uppercase tracking-[0.08em] text-fg-muted">
        <span>Day low</span>
        <span>Day high</span>
      </div>
      <div className="relative h-1.5 rounded-full bg-gradient-to-r from-down/60 via-ink-600 to-up/60">
        <span className="absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-ink-850 bg-fg" style={{ left: `${Math.min(100, Math.max(0, p))}%` }} />
      </div>
    </div>
  );
}

function About({ q }: { q: Quote }) {
  const a = q.about ?? {};
  if (q.asset_class === "stock")
    return (
      <div className="space-y-3 text-sm text-fg-soft">
        <p>
          {q.name} is listed in this demo under the <span className="text-fg">{q.sector}</span> sector. Fundamentals below are illustrative values generated for the demonstration and do not describe the real company.
        </p>
        <div className="grid grid-cols-3 gap-3">
          <MiniStat k="Mkt cap (sim.)" v={inrCompact(Number(a.market_cap_sim))} />
          <MiniStat k="P/E (sim.)" v={String(a.pe_sim)} />
          <MiniStat k="Div. yield (sim.)" v={`${a.div_yield_sim}%`} />
        </div>
      </div>
    );
  if (q.asset_class === "crypto")
    return <p className="text-sm text-fg-soft">Simulated spot market for {q.name} quoted in INR. Purchases update your demo ledger only — there is no wallet address, custody or on-chain settlement. Minimum order {q.min_qty} {q.symbol}.</p>;
  return (
    <p className="text-sm text-fg-soft">
      {q.name}. Simulated margin product with a contract size of {qty(q.lot_size)} {q.symbol.slice(0, 3)} per lot, quoted in {q.currency}. Profit and loss is converted to INR at simulated rates and settled to your demo cash when you reduce or close the position. Availability of currency pairs depends on the jurisdiction configured by the platform administrator.
    </p>
  );
}

function MiniStat({ k, v }: { k: string; v: string }) {
  return (
    <div className="rounded-lg border border-ink-700 bg-ink-900/60 p-3">
      <div className="text-[10px] uppercase tracking-[0.1em] text-fg-muted">{k}</div>
      <div className="num mt-1 text-sm font-semibold text-fg">{v}</div>
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div className="space-y-5">
        <Skeleton className="h-[520px] rounded-2xl" />
        <Skeleton className="h-48 rounded-2xl" />
      </div>
      <Skeleton className="hidden h-[520px] rounded-2xl lg:block" />
    </div>
  );
}
