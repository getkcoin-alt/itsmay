"use client";

import { ArrowDownToLine, ArrowLeftRight, Bitcoin, CandlestickChart, Layers, Plus, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useSession } from "@/components/layout/session";
import { IndexStrip } from "@/components/market/index-strip";
import { AllocationDonut, EquityChart } from "@/components/portfolio/charts";
import { OrdersTable, PositionsTable } from "@/components/trade/tables";
import { AssetIcon, Badge, Card, Change, cx, Empty, Flash, LinkButton, Skeleton, Stat } from "@/components/ui";
import { useApi } from "@/lib/api";
import { assetHref, inr, inrCompact, pct, price, tone } from "@/lib/format";
import { useLiveQuotes } from "@/lib/live";
import type { Order, PositionRow, Quote, Summary } from "@/lib/types";

interface Dashboard {
  summary: Summary;
  positions: PositionRow[];
  top_performers: PositionRow[];
  recent_orders: Order[];
}

interface Overview {
  indices: Quote[];
  gainers: Quote[];
  losers: Quote[];
  crypto_movers: Quote[];
}

export default function DashboardPage() {
  const { user } = useSession();
  const { data } = useApi<Dashboard>("/api/dashboard", { refreshInterval: 8000 });
  const { data: ov } = useApi<Overview>("/api/market/overview", { refreshInterval: 8000 });
  const s = data?.summary;
  const hour = new Date().toLocaleString("en-IN", { hour: "numeric", hour12: false, timeZone: "Asia/Kolkata" });
  const greet = Number(hour) < 12 ? "Good morning" : Number(hour) < 17 ? "Good afternoon" : "Good evening";

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm text-fg-muted">{greet},</p>
          <h1 className="text-[22px] font-semibold tracking-tight text-fg sm:text-[26px]">{user?.full_name ?? " "}</h1>
        </div>
        <div className="flex gap-2">
          <LinkButton href="/wallet?action=deposit" variant="secondary" size="sm"><Plus size={15} /> Add demo funds</LinkButton>
          <LinkButton href="/markets" variant="primary" size="sm">Explore markets</LinkButton>
        </div>
      </div>

      <IndexStrip indices={ov?.indices} />

      {/* Account summary */}
      <Card padded={false}>
        <div className="grid gap-px overflow-hidden rounded-2xl bg-ink-700/60 sm:grid-cols-2 xl:grid-cols-[1.4fr_1fr_1fr_1fr_1fr]">
          <div className="bg-ink-850 p-5">
            <div className="text-[11px] font-medium uppercase tracking-[0.1em] text-fg-muted">Total account value</div>
            {s ? (
              <>
                <div className="num mt-1.5 text-[28px] font-semibold tracking-tight text-fg">{inr(s.total_value)}</div>
                <div className="mt-1 text-sm">
                  <span className={cx("num font-medium", tone(s.day_pnl))}>{inr(s.day_pnl, { sign: true })} ({pct(s.day_pnl_pct)})</span>
                  <span className="text-fg-muted"> today</span>
                </div>
              </>
            ) : (
              <Skeleton className="mt-2 h-9 w-48" />
            )}
          </div>
          {[
            { label: "Available demo cash", value: s && inr(s.available_cash), sub: s && `${inrCompact(s.margin_blocked)} margin · ${inrCompact(s.reserved_for_orders)} reserved` },
            { label: "Invested value", value: s && inr(s.invested_value), sub: s && `Holdings worth ${inrCompact(s.holdings_value)}` },
            { label: "Overall P&L", value: s && inr(s.overall_pnl, { sign: true }), sub: s && `${pct(s.overall_pnl_pct)} on net deposits`, cls: s && tone(s.overall_pnl) },
            { label: "Unrealised · Realised", value: s && inr(s.unrealized_pnl, { sign: true }), sub: s && `Realised ${inr(s.realized_pnl, { sign: true })}`, cls: s && tone(s.unrealized_pnl) },
          ].map((x) => (
            <div key={x.label} className="bg-ink-850 p-5">
              {s ? <Stat label={x.label} value={x.value} sub={x.sub} valueClass={x.cls ?? undefined} /> : <Skeleton className="h-14" />}
            </div>
          ))}
        </div>
      </Card>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="min-w-0 space-y-5">
          <Card title="Performance" subtitle="Trading P&L reconstructed from the simulated ledger and price history">
            <EquityChart />
          </Card>

          <div className="grid gap-3 sm:grid-cols-4">
            {[
              { href: "/stocks", label: "Buy stocks", icon: CandlestickChart, c: "text-brand" },
              { href: "/options", label: "Option chain", icon: Layers, c: "text-[#C58CFF]" },
              { href: "/crypto/BTC", label: "Trade Bitcoin", icon: Bitcoin, c: "text-warn" },
              { href: "/forex/USDINR", label: "USD/INR", icon: ArrowLeftRight, c: "text-[#4FC3D9]" },
            ].map(({ href, label, icon: Icon, c }) => (
              <Link key={href} href={href} className="group flex items-center gap-3 rounded-xl border border-ink-700 bg-ink-850 px-4 py-3.5 transition-colors hover:border-ink-600 hover:bg-ink-800">
                <Icon size={19} className={c} />
                <span className="text-sm font-medium text-fg-soft group-hover:text-fg">{label}</span>
              </Link>
            ))}
          </div>

          <Card title="Open positions" subtitle="Derivatives and forex — marked to simulated prices" action={<Link href="/positions" className="text-xs font-medium text-brand hover:underline">All positions</Link>}>
            {!data ? (
              <Skeleton className="h-40" />
            ) : data.positions.filter((p) => p.asset_class === "option" || p.asset_class === "forex").length ? (
              <PositionsTable rows={data.positions.filter((p) => p.asset_class === "option" || p.asset_class === "forex")} />
            ) : (
              <Empty title="No open derivative or forex positions" action={<LinkButton href="/options" size="sm">Open option chain</LinkButton>} />
            )}
          </Card>

          <Card title="Recent orders" action={<Link href="/orders" className="text-xs font-medium text-brand hover:underline">Order book</Link>}>
            {!data ? <Skeleton className="h-40" /> : data.recent_orders.length ? <OrdersTable orders={data.recent_orders} /> : <Empty title="No orders yet" />}
          </Card>
        </div>

        <div className="space-y-5">
          <Card title="Allocation">
            {s ? <AllocationDonut data={s.allocation} total={s.total_value} /> : <Skeleton className="h-44" />}
            {s && (
              <div className="mt-4 flex items-center justify-between rounded-lg border border-ink-700 bg-ink-900/60 px-3 py-2.5 text-xs">
                <span className="flex items-center gap-1.5 text-fg-muted"><ShieldCheck size={14} className={s.risk_level === "normal" ? "text-up" : "text-warn"} /> Margin utilisation</span>
                <span className="num font-semibold text-fg">{s.margin_utilisation_pct.toFixed(1)}% · <span className="capitalize">{s.risk_level}</span></span>
              </div>
            )}
          </Card>

          <WatchlistCard />

          <Card title="Top performing holdings">
            {!data ? (
              <Skeleton className="h-32" />
            ) : (
              <ul className="space-y-3">
                {data.top_performers.map((p) => (
                  <li key={p.id}>
                    <Link href={assetHref(p.symbol, p.asset_class)} className="flex items-center gap-3">
                      <AssetIcon symbol={p.asset_class === "option" ? p.underlying ?? p.symbol : p.symbol} assetClass={p.asset_class} size={30} />
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium text-fg">{p.asset_class === "option" ? `${p.underlying} ${p.strike} ${p.kind}` : p.symbol}</div>
                        <div className="text-xs text-fg-muted">{inr(p.unrealized_pnl, { sign: true })}</div>
                      </div>
                      <span className={cx("num text-sm font-semibold", tone(p.unrealized_pnl_pct))}>{pct(p.unrealized_pnl_pct)}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="Market movers" action={<Link href="/markets" className="text-xs font-medium text-brand hover:underline">Markets</Link>}>
            {!ov ? <Skeleton className="h-40" /> : (
              <div className="space-y-4">
                <MoverList title="Top gainers" rows={ov.gainers.slice(0, 3)} />
                <MoverList title="Top losers" rows={ov.losers.slice(0, 3)} />
                <MoverList title="Crypto" rows={ov.crypto_movers.slice(0, 3)} />
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

function MoverList({ title, rows }: { title: string; rows: Quote[] }) {
  return (
    <div>
      <div className="mb-2 text-[11px] font-medium uppercase tracking-[0.1em] text-fg-faint">{title}</div>
      <ul className="space-y-2">
        {rows.map((q) => (
          <li key={q.symbol}>
            <Link href={assetHref(q.symbol, q.asset_class)} className="flex items-center gap-2.5 text-sm">
              <AssetIcon symbol={q.symbol} assetClass={q.asset_class} size={24} />
              <span className="flex-1 truncate font-medium text-fg-soft">{q.symbol}</span>
              <span className="num text-fg">{price(q.ltp, q.decimals)}</span>
              <span className={cx("num w-16 text-right text-xs font-semibold", tone(q.change_pct))}>{pct(q.change_pct)}</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

function WatchlistCard() {
  const { data } = useApi<{ items: Quote[] }>("/api/watchlist", { refreshInterval: 6000 });
  const { ticks } = useLiveQuotes((data?.items ?? []).map((i) => i.symbol));
  return (
    <Card title="Watchlist" action={<Link href="/watchlist" className="text-xs font-medium text-brand hover:underline">Manage</Link>}>
      {!data ? (
        <Skeleton className="h-40" />
      ) : data.items.length === 0 ? (
        <Empty title="Your watchlist is empty" body="Star instruments to track them here." icon={<ArrowDownToLine size={20} />} />
      ) : (
        <ul className="-my-1 divide-y divide-ink-750">
          {data.items.slice(0, 7).map((q0) => {
            const q = { ...q0, ...(ticks[q0.symbol] ?? {}) };
            return (
              <li key={q.symbol}>
                <Link href={assetHref(q.symbol, q.asset_class)} className="flex items-center gap-3 py-2.5">
                  <AssetIcon symbol={q.symbol} assetClass={q.asset_class} size={28} />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-fg">{q.symbol}</div>
                    <div className="truncate text-[11px] text-fg-muted">{q.name}</div>
                  </div>
                  <div className="text-right">
                    <div className="num text-sm text-fg"><Flash value={q.ltp}>{price(q.ltp, q.decimals, q.currency)}</Flash></div>
                    <Change percent={q.change_pct} showAbs={false} className="text-[11px]" />
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
