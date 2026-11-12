"use client";

import { Info, ShieldAlert } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { OrderTicket } from "@/components/trade/order-ticket";
import { PositionsTable } from "@/components/trade/tables";
import { Badge, Card, cx, Empty, Flash, Modal, PageHeader, Segmented, SimBadge, Skeleton, Toggle } from "@/components/ui";
import { useApi } from "@/lib/api";
import { compact, expiryLabel, inr, inrCompact, pct, price, tone } from "@/lib/format";
import type { Chain, OptionQuote, PositionRow } from "@/lib/types";

interface Underlying {
  symbol: string;
  name: string;
  spot: number;
  lot_size: number;
  strike_step: number;
  expiries: string[];
  weekly: boolean;
}

export default function OptionsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-[600px] rounded-2xl" />}>
      <OptionsInner />
    </Suspense>
  );
}

function parseSymbol(sym: string | null) {
  if (!sym) return null;
  const p = sym.split("-");
  if (p.length !== 4) return null;
  return { underlying: p[0], expiry: `${p[1].slice(0, 4)}-${p[1].slice(4, 6)}-${p[1].slice(6, 8)}`, strike: Number(p[2]), kind: p[3] as "CE" | "PE" };
}

function OptionsInner() {
  const params = useSearchParams();
  const router = useRouter();
  const pre = parseSymbol(params.get("symbol"));
  const { data: und } = useApi<{ underlyings: Underlying[] }>("/api/market/options/underlyings", { refreshInterval: 30000 });
  const [underlying, setUnderlying] = useState<string>(pre?.underlying ?? params.get("underlying") ?? "NIFTY");
  const current = und?.underlyings.find((u) => u.symbol === underlying);
  const [expiry, setExpiry] = useState<string | null>(pre?.expiry ?? null);
  const [greeks, setGreeks] = useState(false);
  const [selected, setSelected] = useState<{ symbol: string; side: "BUY" | "SELL" } | null>(pre ? { symbol: params.get("symbol")!, side: "BUY" } : null);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [isDesktop, setIsDesktop] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 1280px)");
    const on = () => setIsDesktop(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);

  useEffect(() => {
    if (current && (!expiry || !current.expiries.some((e) => e.startsWith(expiry)))) setExpiry(current.expiries[0].slice(0, 10));
  }, [current, expiry]);

  const { data: chain, error } = useApi<Chain>(expiry ? `/api/market/options/chain/${underlying}?expiry=${expiry}&strikes=12` : null, { refreshInterval: 4000 });
  const { data: pos } = useApi<{ positions: PositionRow[] }>("/api/portfolio/positions", { refreshInterval: 6000 });
  const optionPositions = (pos?.positions ?? []).filter((p) => p.asset_class === "option");

  const contract: OptionQuote | undefined = useMemo(() => {
    if (!selected || !chain) return undefined;
    for (const r of chain.rows) {
      if (r.CE.symbol === selected.symbol) return r.CE;
      if (r.PE.symbol === selected.symbol) return r.PE;
    }
    return undefined;
  }, [selected, chain]);

  const pick = (q: OptionQuote, side: "BUY" | "SELL") => {
    setSelected({ symbol: q.symbol, side });
    setSheetOpen(true);
  };

  const dte = chain ? Math.max(0, (new Date(chain.expiry).getTime() - Date.now()) / 86400000) : 0;

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Derivatives"
        title="Options"
        subtitle="Index and stock options with simulated premiums priced by Black–Scholes on the demo underlying. Buy or write calls (CE) and puts (PE)."
        actions={<SimBadge />}
      />

      <Card padded={false}>
        <div className="flex flex-col gap-4 p-4 sm:p-5 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap items-center gap-4">
            <Segmented
              value={underlying}
              onChange={(v) => {
                setUnderlying(v);
                setExpiry(null);
                setSelected(null);
                router.replace(`/options?underlying=${v}`);
              }}
              options={(und?.underlyings ?? [{ symbol: "NIFTY" } as Underlying]).map((u) => ({ value: u.symbol, label: u.symbol }))}
            />
            {chain && (
              <div>
                <div className="text-[11px] uppercase tracking-[0.1em] text-fg-muted">{chain.underlying_name} · spot (sim.)</div>
                <div className="num text-xl font-semibold text-fg"><Flash value={chain.spot}>{price(chain.spot, 2)}</Flash></div>
              </div>
            )}
          </div>
          {chain && (
            <dl className="grid grid-cols-4 gap-4 text-xs sm:gap-6">
              <Meta k="Lot" v={String(chain.lot_size)} />
              <Meta k="ATM" v={String(chain.atm_strike)} />
              <Meta k="PCR" v={String(chain.pcr ?? "—")} />
              <Meta k="Expiry in" v={`${dte.toFixed(1)}d`} />
            </dl>
          )}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-ink-700 px-4 py-3 sm:px-5">
          <div className="-mx-1 flex gap-1.5 overflow-x-auto px-1">
            {current?.expiries.map((e) => (
              <button
                key={e}
                onClick={() => setExpiry(e.slice(0, 10))}
                className={cx("whitespace-nowrap rounded-lg border px-3 py-1.5 text-xs font-medium", expiry === e.slice(0, 10) ? "border-brand/50 bg-brand-soft text-brand" : "border-ink-700 text-fg-muted hover:text-fg")}
              >
                {expiryLabel(e)}
                <span className="ml-1 text-[10px] opacity-70">{new Date(new Date(e).getTime() + 7 * 86400000).getMonth() !== new Date(e).getMonth() ? "M" : "W"}</span>
              </button>
            ))}
          </div>
          <label className="flex items-center gap-2 text-xs text-fg-muted">
            Greeks <Toggle checked={greeks} onChange={setGreeks} label="Show greeks" />
          </label>
        </div>
      </Card>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <Card padded={false} className="min-w-0 self-start overflow-hidden">
          {error ? (
            <div className="p-5 text-sm text-down">{(error as Error).message}</div>
          ) : !chain ? (
            <div className="space-y-2 p-5">{Array.from({ length: 10 }).map((_, i) => <Skeleton key={i} className="h-9" />)}</div>
          ) : (
            <ChainTable chain={chain} greeks={greeks} onPick={pick} selected={selected?.symbol} />
          )}
        </Card>

        <aside className="hidden xl:block">
          <div className="sticky top-20 space-y-4">
            {contract ? (
              <ContractPanel q={contract} side={selected!.side} />
            ) : (
              <Card>
                <Empty title="Select a contract" body="Hover a premium in the chain and choose B (buy) or S (sell / write) to open the order ticket." icon={<Info size={20} />} />
              </Card>
            )}
          </div>
        </aside>
      </div>

      <Card title="Your option positions" subtitle="Marked to simulated premiums. Expired contracts settle automatically at intrinsic value.">
        {!pos ? <Skeleton className="h-24" /> : optionPositions.length ? <PositionsTable rows={optionPositions} /> : <Empty title="No open option positions" body="Pick a strike above to buy or write an option." />}
      </Card>

      <div className="flex gap-3 rounded-xl border border-down/25 bg-down-soft px-4 py-3 text-[12.5px] leading-relaxed text-[#FFC2C4]">
        <ShieldAlert size={16} className="mt-0.5 shrink-0" />
        <p>
          Options are complex leveraged instruments. Buyers can lose the full premium; writers (sellers) face losses that can far exceed the premium received — theoretically unlimited for uncovered calls. Margins shown are simplified SPAN-style estimates for demonstration only.
        </p>
      </div>

      {/* Mobile / tablet ticket */}
      <Modal open={sheetOpen && !!contract && !isDesktop} onClose={() => setSheetOpen(false)} title={contract ? `${contract.underlying} ${contract.strike} ${contract.kind}` : ""}>
        {contract && <ContractPanel q={contract} side={selected!.side} compact />}
      </Modal>
    </div>
  );
}

function Meta({ k, v }: { k: string; v: string }) {
  return (
    <div>
      <dt className="text-[10px] uppercase tracking-[0.1em] text-fg-muted">{k}</dt>
      <dd className="num mt-0.5 text-sm font-semibold text-fg">{v}</dd>
    </div>
  );
}

function ChainTable({ chain, greeks, onPick, selected }: { chain: Chain; greeks: boolean; onPick: (q: OptionQuote, side: "BUY" | "SELL") => void; selected?: string }) {
  const atmRef = useRef<HTMLTableRowElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const maxOi = Math.max(...chain.rows.flatMap((r) => [r.CE.oi, r.PE.oi]));
  useEffect(() => {
    const el = atmRef.current;
    const box = scroller.current;
    if (el && box) box.scrollTop = el.offsetTop - box.clientHeight / 2 + 20;
  }, [chain.underlying, chain.expiry]);
  const spotIndex = chain.rows.findIndex((r) => r.strike > chain.spot);

  return (
    <div ref={scroller} className="max-h-[640px] overflow-auto">
      <table className="w-full min-w-[640px] border-collapse text-[13px]">
        <thead className="sticky top-0 z-10 bg-ink-850">
          <tr className="text-[10.5px] uppercase tracking-[0.08em] text-fg-muted">
            <th colSpan={greeks ? 5 : 4} className="border-b border-ink-700 py-2 text-center font-semibold text-up/80">Calls (CE)</th>
            <th className="border-b border-ink-700 py-2" />
            <th colSpan={greeks ? 5 : 4} className="border-b border-ink-700 py-2 text-center font-semibold text-down/80">Puts (PE)</th>
          </tr>
          <tr className="text-[10.5px] uppercase tracking-[0.06em] text-fg-faint">
            <th className="border-b border-ink-700 px-2 py-2 text-left font-medium">OI</th>
            <th className="hidden border-b border-ink-700 px-2 py-2 text-right font-medium sm:table-cell">Volume</th>
            <th className="border-b border-ink-700 px-2 py-2 text-right font-medium">IV</th>
            {greeks && <th className="border-b border-ink-700 px-2 py-2 text-right font-medium">Δ</th>}
            <th className="border-b border-ink-700 px-2 py-2 text-right font-medium">LTP</th>
            <th className="border-b border-ink-700 bg-ink-800 px-3 py-2 text-center font-semibold text-fg-soft">Strike</th>
            <th className="border-b border-ink-700 px-2 py-2 text-left font-medium">LTP</th>
            {greeks && <th className="border-b border-ink-700 px-2 py-2 text-left font-medium">Δ</th>}
            <th className="border-b border-ink-700 px-2 py-2 text-left font-medium">IV</th>
            <th className="hidden border-b border-ink-700 px-2 py-2 text-left font-medium sm:table-cell">Volume</th>
            <th className="border-b border-ink-700 px-2 py-2 text-right font-medium">OI</th>
          </tr>
        </thead>
        <tbody>
          {chain.rows.map((r, i) => {
            const ceItm = r.strike < chain.spot;
            const peItm = r.strike > chain.spot;
            const isAtm = r.strike === chain.atm_strike;
            return (
              <FragmentRow key={r.strike} showSpot={i === spotIndex} spot={chain.spot}>
                <tr ref={isAtm ? atmRef : undefined} className="group/row">
                  <OiCell q={r.CE} max={maxOi} itm={ceItm} side="left" />
                  <td className={cx("hidden border-b border-ink-750 px-2 py-2 text-right num text-fg-muted sm:table-cell", ceItm && "bg-up/[0.035]")}>{compact(r.CE.volume)}</td>
                  <td className={cx("border-b border-ink-750 px-2 py-2 text-right num text-fg-muted", ceItm && "bg-up/[0.035]")}>{(r.CE.iv * 100).toFixed(1)}</td>
                  {greeks && <td className={cx("border-b border-ink-750 px-2 py-2 text-right num text-fg-muted", ceItm && "bg-up/[0.035]")}>{r.CE.delta.toFixed(2)}</td>}
                  <PremiumCell q={r.CE} itm={ceItm} align="right" onPick={onPick} selected={selected === r.CE.symbol} />
                  <td className={cx("border-b border-ink-750 bg-ink-800 px-3 py-2 text-center num font-semibold", isAtm ? "text-brand" : "text-fg")}>
                    {r.strike}
                    {isAtm && <div className="text-[9px] font-bold uppercase tracking-[0.1em] text-brand/80">ATM</div>}
                  </td>
                  <PremiumCell q={r.PE} itm={peItm} align="left" onPick={onPick} selected={selected === r.PE.symbol} />
                  {greeks && <td className={cx("border-b border-ink-750 px-2 py-2 text-left num text-fg-muted", peItm && "bg-down/[0.035]")}>{r.PE.delta.toFixed(2)}</td>}
                  <td className={cx("border-b border-ink-750 px-2 py-2 text-left num text-fg-muted", peItm && "bg-down/[0.035]")}>{(r.PE.iv * 100).toFixed(1)}</td>
                  <td className={cx("hidden border-b border-ink-750 px-2 py-2 text-left num text-fg-muted sm:table-cell", peItm && "bg-down/[0.035]")}>{compact(r.PE.volume)}</td>
                  <OiCell q={r.PE} max={maxOi} itm={peItm} side="right" />
                </tr>
              </FragmentRow>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function FragmentRow({ children, showSpot, spot }: { children: React.ReactNode; showSpot: boolean; spot: number }) {
  return (
    <>
      {showSpot && (
        <tr>
          <td colSpan={11} className="p-0">
            <div className="relative flex h-0 items-center justify-center">
              <div className="absolute inset-x-0 h-px bg-brand/50" />
              <span className="relative z-[1] -my-2.5 rounded-full bg-brand px-2 py-0.5 text-[10px] font-bold text-ink-950 num">SPOT {price(spot, 2)}</span>
            </div>
          </td>
        </tr>
      )}
      {children}
    </>
  );
}

function OiCell({ q, max, itm, side }: { q: OptionQuote; max: number; itm: boolean; side: "left" | "right" }) {
  const w = Math.max(4, (q.oi / max) * 100);
  return (
    <td className={cx("relative border-b border-ink-750 px-2 py-2 num", side === "left" ? "text-left" : "text-right", itm && (side === "left" ? "bg-up/[0.035]" : "bg-down/[0.035]"))}>
      <span className={cx("absolute inset-y-1.5 opacity-25", side === "left" ? "left-0 rounded-r bg-up" : "right-0 rounded-l bg-down")} style={{ width: `${w * 0.9}%` }} />
      <span className="relative text-fg-soft">{compact(q.oi / q.lot_size)}</span>
      <div className={cx("relative text-[10px]", tone(q.oi_change_pct))}>{pct(q.oi_change_pct, 1)}</div>
    </td>
  );
}

function PremiumCell({ q, itm, align, onPick, selected }: { q: OptionQuote; itm: boolean; align: "left" | "right"; onPick: (q: OptionQuote, side: "BUY" | "SELL") => void; selected: boolean }) {
  const kindTint = q.kind === "CE" ? "bg-up/[0.035]" : "bg-down/[0.035]";
  return (
    <td className={cx("group/cell relative border-b border-ink-750 px-2 py-1.5 num", align === "right" ? "text-right" : "text-left", itm && kindTint, selected && "outline outline-1 -outline-offset-1 outline-brand")}>
      <button onClick={() => onPick(q, "BUY")} className="w-full text-inherit" aria-label={`Select ${q.symbol}`}>
        <div className="font-semibold text-fg"><Flash value={q.ltp}>{q.ltp.toFixed(2)}</Flash></div>
        <div className={cx("text-[10px]", tone(q.change))}>{pct(q.change_pct, 1)}</div>
      </button>
      <div className={cx("absolute top-1/2 hidden -translate-y-1/2 gap-1 group-hover/cell:flex", align === "right" ? "left-1" : "right-1")}>
        <button onClick={() => onPick(q, "BUY")} className="rounded bg-up px-1.5 py-0.5 text-[10px] font-bold text-ink-950" aria-label={`Buy ${q.symbol}`}>B</button>
        <button onClick={() => onPick(q, "SELL")} className="rounded bg-down px-1.5 py-0.5 text-[10px] font-bold text-white" aria-label={`Sell ${q.symbol}`}>S</button>
      </div>
    </td>
  );
}

function ContractPanel({ q, side, compact: isCompact }: { q: OptionQuote; side: "BUY" | "SELL"; compact?: boolean }) {
  const [view, setView] = useState<"BUY" | "SELL">(side);
  useEffect(() => setView(side), [side, q.symbol]);
  return (
    <div className="space-y-4">
      <div className={cx(!isCompact && "rounded-2xl border border-ink-700/80 bg-ink-850 p-4 shadow-panel")}>
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-[15px] font-semibold text-fg">{q.underlying} {q.strike} {q.kind}</span>
              <Badge tone={q.kind === "CE" ? "up" : "down"}>{q.kind === "CE" ? "Call" : "Put"}</Badge>
            </div>
            <div className="mt-0.5 text-xs text-fg-muted">Expiry {expiryLabel(q.expiry)} · lot {q.lot_size}</div>
          </div>
          <div className="text-right">
            <div className="num text-lg font-semibold text-fg"><Flash value={q.ltp}>{inr(q.ltp)}</Flash></div>
            <div className={cx("num text-xs", tone(q.change))}>{pct(q.change_pct)}</div>
          </div>
        </div>
        <dl className="mt-3 grid grid-cols-4 gap-2 text-center">
          {[["IV", `${(q.iv * 100).toFixed(1)}%`], ["Delta", q.delta.toFixed(2)], ["Theta", q.theta.toFixed(1)], ["Vega", q.vega.toFixed(1)]].map(([k, v]) => (
            <div key={k} className="rounded-lg bg-ink-900/70 py-1.5">
              <dt className="text-[9.5px] uppercase tracking-[0.1em] text-fg-muted">{k}</dt>
              <dd className="num text-[13px] font-semibold text-fg">{v}</dd>
            </div>
          ))}
        </dl>
        <div className="mt-3 text-[11px] text-fg-faint">OI {compact(q.oi)} · Vol {compact(q.volume)} · Spot {price(q.spot, 2)}</div>
      </div>
      <OrderTicket
        compact={isCompact}
        defaultSide={view}
        onPlaced={(o) => setView(o.side)}
        inst={{ symbol: q.symbol, name: `${q.underlying_name} ${q.strike} ${q.kind}`, asset_class: "option", ltp: q.ltp, decimals: 2, currency: "INR", lot_size: q.lot_size, min_qty: q.lot_size }}
      />
      <Card title="Payoff at expiry" subtitle={`${view === "BUY" ? "Long" : "Short"} 1 lot, premium ${inr(q.ltp)}`}>
        <Payoff q={q} side={view} />
      </Card>
    </div>
  );
}

function Payoff({ q, side }: { q: OptionQuote; side: "BUY" | "SELL" }) {
  const pts = useMemo(() => {
    const lo = q.spot * 0.93;
    const hi = q.spot * 1.07;
    const out = [];
    for (let k = 0; k <= 40; k++) {
      const s = lo + ((hi - lo) * k) / 40;
      const intrinsic = q.kind === "CE" ? Math.max(0, s - q.strike) : Math.max(0, q.strike - s);
      const pnl = (intrinsic - q.ltp) * q.lot_size * (side === "BUY" ? 1 : -1);
      out.push({ s: Math.round(s), pnl: Math.round(pnl) });
    }
    return out;
  }, [q, side]);
  const be = q.kind === "CE" ? q.strike + q.ltp : q.strike - q.ltp;
  const maxProfit = side === "BUY" ? (q.kind === "CE" ? "Unlimited" : inr((q.strike - q.ltp) * q.lot_size, { decimals: 0 })) : inr(q.ltp * q.lot_size, { decimals: 0 });
  const maxLoss = side === "BUY" ? inr(q.ltp * q.lot_size, { decimals: 0 }) : q.kind === "CE" ? "Unlimited" : inr((q.strike - q.ltp) * q.lot_size, { decimals: 0 });
  return (
    <div>
      <div className="h-40">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={pts} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
            <CartesianGrid stroke="#1D2433" vertical={false} />
            <XAxis dataKey="s" tick={{ fill: "#566173", fontSize: 10 }} axisLine={false} tickLine={false} minTickGap={30} />
            <YAxis tickFormatter={(v) => inrCompact(v)} tick={{ fill: "#566173", fontSize: 10 }} axisLine={false} tickLine={false} width={52} />
            <Tooltip contentStyle={{ background: "#121722", border: "1px solid #273043", borderRadius: 10, fontSize: 12 }} formatter={(v: number) => [inr(v, { decimals: 0 }), "P&L"]} labelFormatter={(l) => `Spot ${l}`} />
            <ReferenceLine y={0} stroke="#36415A" />
            <ReferenceLine x={Math.round(q.spot)} stroke="#8C9BFF" strokeDasharray="3 3" />
            <Line type="linear" dataKey="pnl" stroke={side === "BUY" ? "#22C993" : "#F2595F"} strokeWidth={2} dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <dl className="mt-3 grid grid-cols-3 gap-2 text-xs">
        <div><dt className="text-fg-muted">Breakeven</dt><dd className="num font-semibold text-fg">{be.toFixed(2)}</dd></div>
        <div><dt className="text-fg-muted">Max profit</dt><dd className="num font-semibold text-up">{maxProfit}</dd></div>
        <div><dt className="text-fg-muted">Max loss</dt><dd className={cx("num font-semibold", maxLoss === "Unlimited" ? "text-down" : "text-fg")}>{maxLoss}</dd></div>
      </dl>
    </div>
  );
}
