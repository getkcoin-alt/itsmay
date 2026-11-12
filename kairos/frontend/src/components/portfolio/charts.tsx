"use client";

import { useState } from "react";
import { Area, AreaChart, Cell, Pie, PieChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApi } from "@/lib/api";
import { ASSET_COLOR, ASSET_LABEL, inr, inrCompact, pct } from "@/lib/format";
import { cx, Segmented, Skeleton } from "../ui";

interface PerfPoint {
  time: number;
  equity: number;
  net_deposits: number;
  pnl: number;
}

export function EquityChart({ height = 240, defaultRange = "1M" }: { height?: number; defaultRange?: "1D" | "1W" | "1M" | "3M" | "ALL" }) {
  const [range, setRange] = useState(defaultRange);
  const { data } = useApi<{ points: PerfPoint[] }>(`/api/portfolio/performance?range=${range}`, { refreshInterval: 30000 });
  const pts = data?.points ?? [];
  const first = pts[0];
  const last = pts[pts.length - 1];
  const change = first && last ? last.pnl - first.pnl : 0;
  const base = first ? first.equity : 1;
  const series = pts.map((p) => ({ ...p, period_pnl: p.pnl - (first?.pnl ?? 0) }));
  const up = change >= 0;
  const color = up ? "#22C993" : "#F2595F";
  const fmtTime = (t: number) =>
    new Date(t * 1000).toLocaleString("en-IN", range === "1D" ? { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" } : { day: "2-digit", month: "short", timeZone: "Asia/Kolkata" });
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm">
          <span className="text-fg-muted">P&L over period </span>
          <span className={cx("num font-semibold", up ? "text-up" : "text-down")}>
            {inr(change, { sign: true })} ({pct((change / base) * 100)})
          </span>
          <span className="ml-1 text-[11px] text-fg-faint">excl. deposits/withdrawals</span>
        </div>
        <Segmented size="sm" value={range} onChange={setRange} options={(["1D", "1W", "1M", "3M", "ALL"] as const).map((r) => ({ value: r, label: r }))} />
      </div>
      <div style={{ height }}>
        {!data ? (
          <Skeleton className="h-full" />
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={series} margin={{ top: 6, right: 4, left: 4, bottom: 0 }}>
              <defs>
                <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={color} stopOpacity={0.28} />
                  <stop offset="100%" stopColor={color} stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="time" tickFormatter={fmtTime} tick={{ fill: "#566173", fontSize: 11 }} axisLine={false} tickLine={false} minTickGap={40} />
              <YAxis dataKey="period_pnl" domain={["auto", "auto"]} tickFormatter={(v) => inrCompact(v)} tick={{ fill: "#566173", fontSize: 11 }} axisLine={false} tickLine={false} width={64} orientation="right" />
              <Tooltip
                cursor={{ stroke: "#36415A" }}
                contentStyle={{ background: "#121722", border: "1px solid #273043", borderRadius: 10, fontSize: 12 }}
                labelFormatter={(t) => new Date(Number(t) * 1000).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}
                formatter={(v: number, _k: string, item: { payload?: PerfPoint }) => [`${inr(v, { sign: true })} · account ${inr(item.payload?.equity ?? 0)}`, "P&L"]}
              />
              <ReferenceLine y={0} stroke="#36415A" strokeDasharray="3 3" />
              <Area type="monotone" dataKey="period_pnl" stroke={color} strokeWidth={2} fill="url(#eq)" isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}

export function AllocationDonut({ data, total }: { data: { asset_class: string; value: number }[]; total: number }) {
  const rows = data.filter((d) => d.value > 0);
  const sum = rows.reduce((a, b) => a + b.value, 0) || 1;
  return (
    <div className="flex flex-col items-center gap-5">
      <div className="relative h-44 w-44 shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie data={rows} dataKey="value" nameKey="asset_class" innerRadius={58} outerRadius={84} paddingAngle={2} stroke="none" isAnimationActive={false}>
              {rows.map((r) => (
                <Cell key={r.asset_class} fill={ASSET_COLOR[r.asset_class] ?? "#566173"} />
              ))}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-[10px] uppercase tracking-[0.12em] text-fg-muted">Account</span>
          <span className="num text-[15px] font-semibold text-fg">{inrCompact(total)}</span>
        </div>
      </div>
      <ul className="w-full space-y-2.5">
        {rows.map((r) => (
          <li key={r.asset_class} className="flex items-center gap-3 text-sm">
            <span className="h-2.5 w-2.5 rounded-sm" style={{ background: ASSET_COLOR[r.asset_class] }} />
            <span className="flex-1 text-fg-soft">{ASSET_LABEL[r.asset_class] ?? r.asset_class}</span>
            <span className="num text-fg">{inrCompact(r.value)}</span>
            <span className="num w-14 text-right text-fg-muted">{((r.value / sum) * 100).toFixed(1)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
