"use client";

import { ShieldAlert } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { api, refreshAll, uid } from "@/lib/api";
import { ASSET_LABEL, assetHref, dateTime, expiryLabel, inr, pct, price, qty, tone } from "@/lib/format";
import type { Order, PositionRow } from "@/lib/types";
import { AssetIcon, Badge, Button, Change, cx, Modal, StatusBadge, Table, Td, Th } from "../ui";
import { useToast } from "../ui/toast";

export function ClosePositionButton({ p, size = "sm" }: { p: PositionRow; size?: "sm" | "md" }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const lots = p.lots !== undefined && (p.asset_class === "option" || p.asset_class === "forex");
  const close = async () => {
    setBusy(true);
    try {
      const body = { symbol: p.symbol, side: p.quantity > 0 ? "SELL" : "BUY", order_type: "MARKET", ...(lots ? { lots: Math.round(Math.abs(p.lots!)) } : { quantity: String(Math.abs(p.quantity)) }), client_order_id: uid() };
      const r = await api<{ order: Order }>("/api/orders", { method: "POST", json: body });
      if (r.order.status === "FILLED") toast({ kind: "success", title: `Closed ${p.symbol}`, body: `Simulated fill @ ${price(r.order.filled_price, p.decimals)} · realised ${inr(r.order.realized_pnl, { sign: true })}` });
      else toast({ kind: "error", title: "Close rejected", body: r.order.reject_reason ?? undefined });
      setOpen(false);
      await refreshAll();
    } catch (e) {
      toast({ kind: "error", title: "Could not close position", body: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <Button size={size} variant="secondary" onClick={() => setOpen(true)}>
        {p.asset_class === "stock" || p.asset_class === "crypto" ? "Exit" : "Close"}
      </Button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={`Close ${p.side.toLowerCase()} position`}
        footer={
          <div className="flex gap-2">
            <Button variant="ghost" className="flex-1" onClick={() => setOpen(false)}>Cancel</Button>
            <Button variant={p.quantity > 0 ? "sell" : "buy"} className="flex-[2]" loading={busy} onClick={close}>
              {p.quantity > 0 ? "Sell" : "Buy back"} at market
            </Button>
          </div>
        }
      >
        <div className="space-y-2 text-sm">
          <div className="font-semibold text-fg">{p.name}</div>
          <div className="flex justify-between"><span className="text-fg-muted">Quantity</span><span className="num">{lots ? `${qty(Math.abs(p.lots!))} lot(s)` : qty(Math.abs(p.quantity))}</span></div>
          <div className="flex justify-between"><span className="text-fg-muted">Avg / LTP (sim.)</span><span className="num">{price(p.avg_price, p.decimals)} / {price(p.ltp, p.decimals)}</span></div>
          <div className="flex justify-between"><span className="text-fg-muted">Estimated P&L</span><span className={cx("num font-semibold", tone(p.unrealized_pnl))}>{inr(p.unrealized_pnl, { sign: true })}</span></div>
          <p className="pt-2 text-xs text-fg-muted">A market order for the full quantity will be sent to the simulated engine. The final price may differ slightly as the simulated market moves.</p>
        </div>
      </Modal>
    </>
  );
}

export function HoldingsTable({ rows }: { rows: PositionRow[] }) {
  return (
    <>
      <ul className="divide-y divide-ink-750 md:hidden">
        {rows.map((p) => (
          <li key={p.id} className="py-3">
            <Link href={assetHref(p.symbol, p.asset_class)} className="flex items-center gap-3">
              <AssetIcon symbol={p.symbol} assetClass={p.asset_class} />
              <div className="min-w-0 flex-1">
                <div className="text-sm font-semibold text-fg">{p.symbol}</div>
                <div className="text-xs text-fg-muted">{qty(p.quantity)} @ {price(p.avg_price, p.decimals)}</div>
              </div>
              <div className="text-right">
                <div className="num text-sm text-fg">{inr(p.market_value)}</div>
                <div className={cx("num text-xs", tone(p.unrealized_pnl))}>{inr(p.unrealized_pnl, { sign: true })} ({pct(p.unrealized_pnl_pct)})</div>
              </div>
            </Link>
          </li>
        ))}
      </ul>
      <div className="hidden md:block">
        <Table>
          <thead>
            <tr>
              <Th>Instrument</Th>
              <Th align="right">Qty</Th>
              <Th align="right">Avg cost</Th>
              <Th align="right">LTP (sim.)</Th>
              <Th align="right">Invested</Th>
              <Th align="right">Current</Th>
              <Th align="right">P&L</Th>
              <Th align="right">Today</Th>
              <Th align="right" />
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.id} className="hover:bg-ink-800/60">
                <Td>
                  <Link href={assetHref(p.symbol, p.asset_class)} className="flex items-center gap-3 hover:text-brand">
                    <AssetIcon symbol={p.symbol} assetClass={p.asset_class} size={28} />
                    <div>
                      <div className="font-semibold text-fg">{p.symbol}</div>
                      <div className="max-w-[200px] truncate text-xs text-fg-muted">{p.name}</div>
                    </div>
                  </Link>
                </Td>
                <Td align="right">{qty(p.quantity)}</Td>
                <Td align="right">{price(p.avg_price, p.decimals)}</Td>
                <Td align="right" className="text-fg">{price(p.ltp, p.decimals)}</Td>
                <Td align="right">{inr(p.invested)}</Td>
                <Td align="right" className="text-fg">{inr(p.market_value)}</Td>
                <Td align="right">
                  <div className={tone(p.unrealized_pnl)}>{inr(p.unrealized_pnl, { sign: true })}</div>
                  <div className={cx("text-xs", tone(p.unrealized_pnl))}>{pct(p.unrealized_pnl_pct)}</div>
                </Td>
                <Td align="right" className={tone(p.day_pnl)}>{inr(p.day_pnl, { sign: true })}</Td>
                <Td align="right"><ClosePositionButton p={p} /></Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </div>
    </>
  );
}

export function PositionsTable({ rows, showClose = true }: { rows: PositionRow[]; showClose?: boolean }) {
  return (
    <>
      <ul className="space-y-3 md:hidden">
        {rows.map((p) => (
          <li key={p.id} className="rounded-xl border border-ink-700 bg-ink-900/50 p-3">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <Badge tone={p.side === "LONG" ? "up" : "down"}>{p.side}</Badge>
                  <span className="truncate text-sm font-semibold text-fg">{p.asset_class === "option" ? p.name : p.symbol}</span>
                </div>
                <div className="mt-1 text-xs text-fg-muted">
                  {qty(Math.abs(p.lots ?? p.quantity))} {p.lots !== undefined ? "lot(s)" : ""} · avg {price(p.avg_price, p.decimals)} · LTP {price(p.ltp, p.decimals)}
                </div>
              </div>
              <div className={cx("num text-right text-sm font-semibold", tone(p.unrealized_pnl))}>{inr(p.unrealized_pnl, { sign: true })}</div>
            </div>
            <div className="mt-2 flex items-center justify-between text-xs text-fg-muted">
              <span>Margin {inr(p.margin_blocked)}{p.days_to_expiry !== undefined ? ` · ${p.days_to_expiry.toFixed(1)}d to expiry` : ""}</span>
              {showClose && <ClosePositionButton p={p} />}
            </div>
          </li>
        ))}
      </ul>
      <div className="hidden md:block">
        <Table>
          <thead>
            <tr>
              <Th>Contract</Th>
              <Th>Side</Th>
              <Th align="right">Qty</Th>
              <Th align="right">Avg</Th>
              <Th align="right">LTP (sim.)</Th>
              <Th align="right">Unrealised P&L</Th>
              <Th align="right">Margin</Th>
              <Th align="right">Risk</Th>
              {showClose && <Th align="right" />}
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.id} className="hover:bg-ink-800/60">
                <Td>
                  <Link href={assetHref(p.symbol, p.asset_class)} className="hover:text-brand">
                    <div className="font-semibold text-fg">{p.asset_class === "option" ? `${p.underlying} ${p.strike} ${p.kind}` : p.symbol}</div>
                    <div className="text-xs text-fg-muted">
                      {p.asset_class === "option" ? `Exp ${expiryLabel(p.expiry!)} · spot ${price(p.spot, 2)} · Δ ${p.delta?.toFixed(2)}` : `${p.name}`}
                    </div>
                  </Link>
                </Td>
                <Td><Badge tone={p.side === "LONG" ? "up" : "down"}>{p.side}</Badge></Td>
                <Td align="right">
                  {qty(Math.abs(p.lots ?? p.quantity))}
                  <span className="text-xs text-fg-muted"> {p.lots !== undefined ? "lots" : ""}</span>
                </Td>
                <Td align="right">{price(p.avg_price, p.decimals)}</Td>
                <Td align="right" className="text-fg">{price(p.ltp, p.decimals)}</Td>
                <Td align="right">
                  <div className={tone(p.unrealized_pnl)}>{inr(p.unrealized_pnl, { sign: true })}</div>
                  <div className={cx("text-xs", tone(p.day_pnl))}>today {inr(p.day_pnl, { sign: true })}</div>
                </Td>
                <Td align="right">{p.margin_blocked > 0 ? inr(p.margin_blocked) : "—"}</Td>
                <Td align="right"><RiskTag p={p} /></Td>
                {showClose && <Td align="right"><ClosePositionButton p={p} /></Td>}
              </tr>
            ))}
          </tbody>
        </Table>
      </div>
    </>
  );
}

function RiskTag({ p }: { p: PositionRow }) {
  if (p.asset_class === "option" && p.quantity < 0 && p.kind === "CE")
    return <span className="inline-flex items-center gap-1 text-xs font-semibold text-down"><ShieldAlert size={13} /> Unlimited</span>;
  if (p.asset_class === "option" && p.quantity < 0) return <span className="text-xs font-semibold text-warn">High</span>;
  if (p.asset_class === "forex") return <span className="text-xs font-semibold text-warn">Leveraged</span>;
  return <span className="text-xs text-fg-muted">Limited to premium</span>;
}

export function OrdersTable({ orders, onCancel, showUser = false }: { orders: Order[]; onCancel?: (o: Order) => void; showUser?: boolean }) {
  return (
    <>
      <ul className="divide-y divide-ink-750 md:hidden">
        {orders.map((o) => (
          <li key={o.id} className="py-3">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className={cx("text-xs font-bold", o.side === "BUY" ? "text-up" : "text-down")}>{o.side}</span>
                  <span className="truncate text-sm font-semibold text-fg">{o.asset_class === "option" ? o.name : o.symbol}</span>
                </div>
                <div className="mt-0.5 text-xs text-fg-muted">
                  {qty(o.lots ?? o.quantity)}{o.lots ? " lot(s)" : ""} · {o.order_type}{o.limit_price ? ` @ ${price(o.limit_price, 2)}` : ""} · {dateTime(o.created_at)}
                </div>
                {o.reject_reason && <div className="mt-1 text-xs text-down">{o.reject_reason}</div>}
              </div>
              <div className="flex flex-col items-end gap-1">
                <StatusBadge status={o.status} />
                {o.filled_price !== null && <span className="num text-xs text-fg-soft">@ {price(o.filled_price, 2)}</span>}
                {o.status === "OPEN" && onCancel && <button onClick={() => onCancel(o)} className="text-xs font-medium text-down">Cancel</button>}
              </div>
            </div>
          </li>
        ))}
      </ul>
      <div className="hidden md:block">
        <Table>
          <thead>
            <tr>
              <Th>Time</Th>
              {showUser && <Th>User</Th>}
              <Th>Instrument</Th>
              <Th>Side</Th>
              <Th>Type</Th>
              <Th align="right">Qty</Th>
              <Th align="right">Price</Th>
              <Th align="right">Fees</Th>
              <Th align="right">Realised</Th>
              <Th>Status</Th>
              {onCancel && <Th align="right" />}
            </tr>
          </thead>
          <tbody>
            {orders.map((o) => (
              <tr key={o.id} className="hover:bg-ink-800/60">
                <Td className="text-fg-muted">{dateTime(o.created_at)}</Td>
                {showUser && <Td className="text-fg-soft">{o.user_email}</Td>}
                <Td>
                  <div className="font-medium text-fg">{o.asset_class === "option" ? o.name : o.symbol}</div>
                  <div className="text-xs text-fg-muted">{ASSET_LABEL[o.asset_class] ?? o.asset_class} · {o.product}</div>
                </Td>
                <Td className={cx("font-semibold", o.side === "BUY" ? "text-up" : "text-down")}>{o.side}</Td>
                <Td className="text-fg-soft">{o.order_type}</Td>
                <Td align="right">{qty(o.lots ?? o.quantity)}{o.lots ? <span className="text-xs text-fg-muted"> lots</span> : null}</Td>
                <Td align="right">
                  {o.filled_price !== null ? price(o.filled_price, 2) : o.limit_price !== null ? <span className="text-fg-muted">lmt {price(o.limit_price, 2)}</span> : "—"}
                </Td>
                <Td align="right" className="text-fg-muted">{o.fees ? inr(o.fees) : "—"}</Td>
                <Td align="right" className={tone(o.realized_pnl)}>{o.realized_pnl ? inr(o.realized_pnl, { sign: true }) : "—"}</Td>
                <Td>
                  <div className="flex flex-col gap-0.5">
                    <StatusBadge status={o.status} />
                    {o.reject_reason && <span className="max-w-[240px] truncate text-[11px] text-down" title={o.reject_reason}>{o.reject_reason}</span>}
                  </div>
                </Td>
                {onCancel && (
                  <Td align="right">
                    {o.status === "OPEN" && (
                      <Button size="sm" variant="danger" onClick={() => onCancel(o)}>Cancel</Button>
                    )}
                  </Td>
                )}
              </tr>
            ))}
          </tbody>
        </Table>
      </div>
    </>
  );
}

export { Change };
