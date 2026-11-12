"use client";

import { AlertTriangle, CheckCircle2, Clock, Info, ShieldAlert, XCircle } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, refreshAll, uid, useApi } from "@/lib/api";
import { inr, price, qty as fmtQty } from "@/lib/format";
import type { AssetClass, Order, PositionRow, Preview, Warning } from "@/lib/types";
import { useSession } from "../layout/session";
import { Button, cx, Input, Modal, Segmented } from "../ui";
import { useToast } from "../ui/toast";

export interface TicketInstrument {
  symbol: string;
  name: string;
  asset_class: AssetClass;
  ltp: number;
  decimals: number;
  currency: string;
  lot_size: number;
  min_qty: number;
  tradable?: boolean;
}

const usesLots = (ac: AssetClass) => ac === "option" || ac === "forex";

export function OrderTicket({ inst, defaultSide = "BUY", compact = false, onPlaced }: { inst: TicketInstrument; defaultSide?: "BUY" | "SELL"; compact?: boolean; onPlaced?: (o: Order) => void }) {
  const [side, setSide] = useState<"BUY" | "SELL">(defaultSide);
  const { user } = useSession();
  const [type, setType] = useState<"MARKET" | "LIMIT">(user?.preferences?.default_order_type === "LIMIT" ? "LIMIT" : "MARKET");
  const [amount, setAmount] = useState<string>(usesLots(inst.asset_class) ? "1" : inst.asset_class === "crypto" ? String(inst.min_qty * 10) : "1");
  const [limit, setLimit] = useState<string>("");
  const [preview, setPreview] = useState<Preview | null>(null);
  // The exact request the current preview was computed for. Review is only allowed
  // when it matches the current inputs, so the dialog never shows a stale order.
  const [previewFor, setPreviewFor] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [result, setResult] = useState<Order | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [ack, setAck] = useState(false);
  const toast = useToast();
  const { data: posData } = useApi<{ positions: PositionRow[] }>("/api/portfolio/positions");
  const held = posData?.positions.find((p) => p.symbol === inst.symbol);
  const lots = usesLots(inst.asset_class);
  const seq = useRef(0);

  useEffect(() => setSide(defaultSide), [defaultSide]);
  useEffect(() => {
    setLimit((l) => l || inst.ltp.toFixed(inst.decimals));
  }, [inst.ltp, inst.decimals]);
  useEffect(() => {
    setResult(null);
    setLimit(inst.ltp.toFixed(inst.decimals));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inst.symbol]);

  const body = useMemo(() => {
    const n = Number(amount);
    if (!amount || !Number.isFinite(n) || n <= 0) return null;
    return {
      symbol: inst.symbol,
      side,
      order_type: type,
      ...(lots ? { lots: Math.floor(n) } : { quantity: amount }),
      ...(type === "LIMIT" ? { limit_price: limit } : {}),
    };
  }, [amount, inst.symbol, side, type, limit, lots]);
  const bodyKey = body ? JSON.stringify(body) : null;

  // Live server-side preview (debounced). Re-runs as the simulated price moves.
  useEffect(() => {
    if (!body) {
      setPreview(null);
      setPreviewError(amount ? "Enter a valid quantity" : null);
      return;
    }
    const my = ++seq.current;
    const t = setTimeout(async () => {
      try {
        const p = await api<Preview>("/api/orders/preview", { method: "POST", json: body });
        if (my === seq.current) {
          setPreview(p);
          setPreviewFor(JSON.stringify(body));
          setPreviewError(null);
        }
      } catch (e) {
        if (my === seq.current) {
          setPreview(null);
          setPreviewError((e as Error).message);
        }
      }
    }, 250);
    return () => clearTimeout(t);
  }, [body, amount, Math.round(inst.ltp * 100)]); // eslint-disable-line react-hooks/exhaustive-deps

  const fresh = !!preview && previewFor !== null && previewFor === bodyKey;
  const dangers = preview?.warnings.filter((w) => w.level === "danger") ?? [];

  const submit = async () => {
    if (!body || !fresh) return; // never submit something other than what was reviewed
    setSubmitting(true);
    try {
      const r = await api<{ order: Order }>("/api/orders", { method: "POST", json: { ...body, client_order_id: uid() } });
      setResult(r.order);
      setReviewOpen(false);
      setAck(false);
      await refreshAll();
      onPlaced?.(r.order);
      const o = r.order;
      if (o.status === "FILLED") toast({ kind: "success", title: `${o.side === "BUY" ? "Bought" : "Sold"} ${fmtQty(o.lots ?? o.quantity)}${o.lots ? " lot(s)" : ""} ${o.symbol}`, body: `Simulated fill @ ${price(o.filled_price, inst.decimals, inst.currency)}` });
      else if (o.status === "OPEN") toast({ kind: "info", title: "Limit order placed", body: `Resting at ${price(o.limit_price, inst.decimals, inst.currency)}` });
      else toast({ kind: "error", title: "Order rejected", body: o.reject_reason ?? undefined });
    } catch (e) {
      toast({ kind: "error", title: "Could not place order", body: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setSubmitting(false);
    }
  };

  const unit = lots ? "lots" : inst.asset_class === "crypto" ? inst.symbol : "shares";
  const step = lots ? 1 : inst.min_qty;
  const isSellLong = side === "SELL" && (inst.asset_class === "stock" || inst.asset_class === "crypto");
  const disabled = inst.tradable === false;

  const quick = (frac: number) => {
    if (!held || held.quantity <= 0) return;
    const units = held.quantity * frac;
    if (lots) setAmount(String(Math.max(1, Math.floor((held.lots ?? units) * frac))));
    else setAmount(String(Math.max(inst.min_qty, Math.floor(units / inst.min_qty) * inst.min_qty).toFixed(Math.max(0, -Math.floor(Math.log10(inst.min_qty))))));
  };

  return (
    <div className={cx(!compact && "rounded-2xl border border-ink-700/80 bg-ink-850 p-4 shadow-panel sm:p-5")}>
      <div className="grid grid-cols-2 gap-1 rounded-xl bg-ink-900 p-1">
        {(["BUY", "SELL"] as const).map((s) => (
          <button
            key={s}
            onClick={() => setSide(s)}
            className={cx("h-10 rounded-lg text-sm font-semibold transition-colors", side === s ? (s === "BUY" ? "bg-up text-ink-950" : "bg-down text-white") : "text-fg-muted hover:text-fg")}
          >
            {s === "BUY" ? (inst.asset_class === "forex" ? "Buy / Long" : "Buy") : inst.asset_class === "forex" ? "Sell / Short" : inst.asset_class === "option" ? "Sell / Write" : "Sell"}
          </button>
        ))}
      </div>

      <div className="mt-4 flex items-center justify-between">
        <Segmented size="sm" value={type} onChange={setType} options={[{ value: "MARKET", label: "Market" }, { value: "LIMIT", label: "Limit" }]} />
        <span className="text-xs text-fg-muted">
          {inst.asset_class === "stock" ? "Delivery" : inst.asset_class === "crypto" ? "Spot" : inst.asset_class === "forex" ? "Margin" : "NRML"} · Simulated
        </span>
      </div>

      <div className="mt-4 space-y-3">
        <Input
          label={lots ? `Lots (1 lot = ${fmtQty(inst.lot_size)} ${inst.asset_class === "forex" ? inst.symbol.slice(0, 3) : "units"})` : `Quantity (${unit})`}
          type="number"
          inputMode="decimal"
          min={step}
          step={step}
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          suffix={lots && Number(amount) > 0 ? `${fmtQty(Number(amount) * inst.lot_size)} units` : undefined}
          hint={held && held.quantity !== 0 ? `Position: ${held.side === "SHORT" ? "short " : ""}${fmtQty(Math.abs(lots ? held.lots ?? held.quantity : held.quantity))} ${unit} @ avg ${price(held.avg_price, inst.decimals)}` : inst.asset_class === "crypto" ? `Minimum ${inst.min_qty} ${inst.symbol}` : undefined}
        />
        {isSellLong && held && held.quantity > 0 && (
          <div className="flex gap-1.5">
            {[0.25, 0.5, 1].map((f) => (
              <button key={f} onClick={() => quick(f)} className="rounded-md border border-ink-600 px-2 py-1 text-xs text-fg-soft hover:border-ink-500 hover:text-fg">
                {f === 1 ? "All" : `${f * 100}%`}
              </button>
            ))}
          </div>
        )}
        {type === "LIMIT" && (
          <Input label={`Limit price (${inst.currency})`} type="number" inputMode="decimal" step={Math.pow(10, -inst.decimals)} value={limit} onChange={(e) => setLimit(e.target.value)} hint={`Market (sim.): ${price(inst.ltp, inst.decimals, inst.currency)}`} />
        )}
      </div>

      <dl className="mt-4 space-y-2 rounded-xl border border-ink-700 bg-ink-900/60 p-3 text-[13px]">
        <Row label={type === "MARKET" ? "Est. execution price" : "Execution price"} value={preview ? price(preview.price, inst.decimals, inst.currency) : "—"} />
        <Row label={inst.asset_class === "forex" ? "Notional (INR)" : side === "BUY" ? "Order value" : "Proceeds"} value={preview ? inr(preview.notional_inr) : "—"} />
        {preview && preview.margin_required > 0 && <Row label="Margin to block" value={inr(preview.margin_required)} strong />}
        {preview && preview.margin_released > 0 && <Row label="Margin released" value={inr(preview.margin_released)} />}
        <Row label="Simulated fees" value={preview ? inr(preview.fees) : "—"} />
        <Row label="Available after" value={preview ? inr(preview.available_after) : "—"} tone={preview && preview.available_after < 0 ? "text-down" : undefined} />
      </dl>

      {preview?.warnings.filter((w) => w.level !== "info").slice(0, 1).map((w) => <WarningNote key={w.code} w={w} />)}
      {(previewError || (preview && !preview.can_execute)) && (
        <div className="mt-3 flex gap-2 rounded-lg border border-down/30 bg-down-soft px-3 py-2 text-[13px] text-down">
          <XCircle size={16} className="mt-0.5 shrink-0" />
          <span>{previewError || preview?.rejection_reason}</span>
        </div>
      )}

      <Button
        variant={side === "BUY" ? "buy" : "sell"}
        size="lg"
        className="mt-4 w-full"
        disabled={disabled || !fresh || !preview?.can_execute}
        onClick={() => setReviewOpen(true)}
      >
        {disabled ? "Trading disabled" : body && !fresh && !previewError ? "Updating preview…" : `Review ${side === "BUY" ? "buy" : "sell"} order`}
      </Button>
      <p className="mt-2 text-center text-[11px] text-fg-faint">Simulated execution against demo prices. No real order is sent to any exchange.</p>

      {result && <ResultCard order={result} decimals={inst.decimals} currency={inst.currency} onDismiss={() => setResult(null)} />}

      <Modal
        open={reviewOpen}
        onClose={() => setReviewOpen(false)}
        title="Review simulated order"
        footer={
          <div className="flex gap-2">
            <Button variant="ghost" className="flex-1" onClick={() => setReviewOpen(false)}>
              Edit
            </Button>
            <Button variant={side === "BUY" ? "buy" : "sell"} className="flex-[2]" loading={submitting} disabled={!fresh || (dangers.length > 0 && !ack)} onClick={submit}>
              Confirm {side === "BUY" ? "buy" : "sell"}
            </Button>
          </div>
        }
      >
        {preview && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <div className={cx("text-xs font-bold uppercase tracking-[0.12em]", side === "BUY" ? "text-up" : "text-down")}>{side} · {type}</div>
                <div className="mt-0.5 text-lg font-semibold text-fg">{preview.name}</div>
                <div className="text-xs text-fg-muted">{preview.symbol} · {preview.product}</div>
              </div>
            </div>
            <dl className="space-y-2 rounded-xl border border-ink-700 bg-ink-900/60 p-3 text-[13px]">
              <Row label="Quantity" value={lots ? `${fmtQty(preview.lots ?? 0)} lot(s) · ${fmtQty(preview.quantity)} units` : `${fmtQty(preview.quantity)} ${unit}`} />
              <Row label={type === "LIMIT" && !preview.marketable ? "Limit price" : "Price (sim.)"} value={price(preview.price, inst.decimals, inst.currency)} />
              <Row label={inst.asset_class === "forex" ? "Notional (INR)" : "Value"} value={inr(preview.notional_inr)} />
              {preview.margin_required > 0 && <Row label="Margin blocked" value={inr(preview.margin_required)} strong />}
              <Row label="Fees (simulated)" value={inr(preview.fees)} />
              {preview.realized_pnl !== 0 && <Row label="Realised P&L on close" value={inr(preview.realized_pnl, { sign: true })} tone={preview.realized_pnl >= 0 ? "text-up" : "text-down"} />}
              <Row label="Available cash after" value={inr(preview.available_after)} />
              <Row label="Position after" value={`${preview.position_after > 0 ? "Long " : preview.position_after < 0 ? "Short " : ""}${fmtQty(Math.abs(preview.position_after))} units`} />
            </dl>
            {type === "LIMIT" && !preview.marketable && (
              <p className="flex gap-2 text-[13px] text-fg-soft">
                <Clock size={15} className="mt-0.5 shrink-0 text-brand" /> Order will rest as OPEN and fill automatically when the simulated price reaches your limit.
              </p>
            )}
            {preview.warnings.map((w) => <WarningNote key={w.code} w={w} />)}
            {dangers.length > 0 && (
              <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-down/30 bg-down-soft/60 p-3 text-[13px] text-fg">
                <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-0.5 h-4 w-4 accent-[#F2595F]" />
                I understand the risk described above, including that losses can exceed the premium received and margin blocked.
              </label>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}

function Row({ label, value, strong, tone }: { label: string; value: string; strong?: boolean; tone?: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-fg-muted">{label}</dt>
      <dd className={cx("num text-right", strong ? "font-semibold text-fg" : "text-fg-soft", tone)}>{value}</dd>
    </div>
  );
}

export function WarningNote({ w }: { w: Warning }) {
  const Icon = w.level === "danger" ? ShieldAlert : w.level === "warning" ? AlertTriangle : Info;
  return (
    <div className={cx("mt-3 flex gap-2 rounded-lg border px-3 py-2 text-[12.5px] leading-relaxed", w.level === "danger" ? "border-down/35 bg-down-soft text-[#FFB3B6]" : w.level === "warning" ? "border-warn/30 bg-warn-soft text-[#F7D79B]" : "border-brand/25 bg-brand-soft text-fg-soft")}>
      <Icon size={15} className="mt-0.5 shrink-0" />
      <span>{w.message}</span>
    </div>
  );
}

function ResultCard({ order, decimals, currency, onDismiss }: { order: Order; decimals: number; currency: string; onDismiss: () => void }) {
  const filled = order.status === "FILLED";
  const open = order.status === "OPEN";
  const Icon = filled ? CheckCircle2 : open ? Clock : XCircle;
  return (
    <div className={cx("mt-4 animate-rise rounded-xl border p-3.5", filled ? "border-up/30 bg-up-soft" : open ? "border-brand/30 bg-brand-soft" : "border-down/30 bg-down-soft")} role="status">
      <div className="flex items-start gap-2.5">
        <Icon size={18} className={cx("mt-0.5 shrink-0", filled ? "text-up" : open ? "text-brand" : "text-down")} />
        <div className="min-w-0 flex-1 text-[13px]">
          <div className="font-semibold text-fg">{filled ? "Order executed (simulated)" : open ? "Limit order open" : "Order rejected"}</div>
          <div className="mt-0.5 text-fg-soft">
            {order.side} {fmtQty(order.lots ?? order.quantity)} {order.lots ? "lot(s)" : ""} {order.symbol}
            {filled && <> @ {price(order.filled_price, decimals, currency)} · fees {inr(order.fees)}</>}
            {open && <> @ limit {price(order.limit_price, decimals, currency)}</>}
          </div>
          {order.reject_reason && <div className="mt-1 text-down">{order.reject_reason}</div>}
          <div className="mt-1 text-[11px] text-fg-muted">Order ID {order.id.slice(0, 10).toUpperCase()}</div>
          <div className="mt-2 flex gap-3 text-xs font-medium">
            <Link href="/orders" className="text-brand hover:underline">View orders</Link>
            <Link href={order.asset_class === "option" || order.asset_class === "forex" ? "/positions" : "/portfolio"} className="text-brand hover:underline">View {order.asset_class === "option" || order.asset_class === "forex" ? "positions" : "portfolio"}</Link>
            <button onClick={onDismiss} className="ml-auto text-fg-muted hover:text-fg">Dismiss</button>
          </div>
        </div>
      </div>
    </div>
  );
}
