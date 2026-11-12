"use client";

import Link from "next/link";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, PageHeader, SimBadge, Skeleton, Stat } from "@/components/ui";
import { useApi } from "@/lib/api";
import { ASSET_COLOR, ASSET_LABEL, dateTime, inr, inrCompact } from "@/lib/format";

interface Stats {
  users_total: number;
  active_24h: number;
  active_7d: number;
  disabled_users: number;
  kyc_pending: number;
  orders_24h: number;
  order_status: Record<string, number>;
  open_positions: number;
  demo_cash_outstanding: number;
  by_asset_class: { asset_class: string; orders: number; fees: number }[];
  fees_total: number;
}
interface Reports { daily: { date: string; orders: number; filled: number; rejected: number; fees: number; signups: number }[] }
interface Log { id: string; actor_email: string | null; action: string; target_type: string | null; target_id: string | null; created_at: string }

export default function AdminOverview() {
  const { data: s } = useApi<Stats>("/api/admin/stats", { refreshInterval: 15000 });
  const { data: r } = useApi<Reports>("/api/admin/reports?days=30");
  const { data: logs } = useApi<{ logs: Log[] }>("/api/admin/audit-logs?limit=8", { refreshInterval: 15000 });
  const tiles: [string, string | number | undefined, string?, string?][] = [
    ["Registered users", s?.users_total, `${s?.disabled_users ?? 0} disabled`, "/admin/users"],
    ["Active (24h)", s?.active_24h, `${s?.active_7d ?? 0} in the last 7 days`],
    ["KYC pending", s?.kyc_pending, "Awaiting review", "/admin/kyc"],
    ["Orders (24h)", s?.orders_24h, `${s?.order_status?.OPEN ?? 0} resting limit orders`, "/admin/orders"],
    ["Open positions", s?.open_positions, "Across all users"],
    ["Simulated fees", s ? inr(s.fees_total) : undefined, "All-time, all users"],
  ];
  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Back office" title="Platform overview" subtitle="Demo trading activity across all registered users." actions={<SimBadge />} />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
        {tiles.map(([k, v, sub, href]) => {
          const inner = s ? <Stat label={k} value={v} sub={sub} /> : <Skeleton className="h-14" />;
          return <Card key={k} className="!rounded-xl">{href ? <Link href={href}>{inner}</Link> : inner}</Card>;
        })}
      </div>
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
        <Card title="Order activity · last 30 days">
          <div className="h-64">
            {!r ? <Skeleton className="h-full" /> : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={r.daily} margin={{ top: 4, right: 0, left: -18, bottom: 0 }}>
                  <XAxis dataKey="date" tickFormatter={(d) => d.slice(5)} tick={{ fill: "#566173", fontSize: 10 }} axisLine={false} tickLine={false} minTickGap={16} />
                  <YAxis tick={{ fill: "#566173", fontSize: 10 }} axisLine={false} tickLine={false} allowDecimals={false} />
                  <Tooltip cursor={{ fill: "#1D2433" }} contentStyle={{ background: "#121722", border: "1px solid #273043", borderRadius: 10, fontSize: 12 }} />
                  <Bar dataKey="filled" stackId="a" fill="#22C993" name="Filled" radius={[0, 0, 0, 0]} />
                  <Bar dataKey="rejected" stackId="a" fill="#F2595F" name="Rejected" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>
        <Card title="Activity by asset class">
          {!s ? <Skeleton className="h-48" /> : (
            <ul className="space-y-3">
              {s.by_asset_class.map((a) => (
                <li key={a.asset_class} className="flex items-center gap-3 text-sm">
                  <span className="h-2.5 w-2.5 rounded-sm" style={{ background: ASSET_COLOR[a.asset_class] }} />
                  <span className="flex-1 text-fg-soft">{ASSET_LABEL[a.asset_class] ?? a.asset_class}</span>
                  <span className="num text-fg">{a.orders} fills</span>
                  <span className="num w-20 text-right text-fg-muted">{inrCompact(a.fees)}</span>
                </li>
              ))}
              <li className="border-t border-ink-700 pt-3 text-xs text-fg-muted">Demo cash held across accounts: <span className="num text-fg">{inr(s.demo_cash_outstanding)}</span></li>
            </ul>
          )}
        </Card>
      </div>
      <Card title="Recent audit events" action={<Link href="/admin/reports" className="text-xs font-medium text-brand hover:underline">Full audit log</Link>}>
        {!logs ? <Skeleton className="h-40" /> : (
          <ul className="divide-y divide-ink-750 text-sm">
            {logs.logs.map((l) => (
              <li key={l.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5">
                <span className="font-mono text-xs text-brand">{l.action}</span>
                <span className="flex-1 truncate px-2 text-fg-soft">{l.actor_email ?? "anonymous"}{l.target_id ? ` → ${l.target_type} ${l.target_id.slice(0, 12)}` : ""}</span>
                <span className="text-xs text-fg-muted">{dateTime(l.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
