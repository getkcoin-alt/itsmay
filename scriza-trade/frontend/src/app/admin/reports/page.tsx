"use client";

import { useState } from "react";
import { Bar, BarChart, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, Input, PageHeader, Segmented, Skeleton, Table, Td, Th } from "@/components/ui";
import { useApi } from "@/lib/api";
import { dateTime, inr } from "@/lib/format";

interface Reports {
  daily: { date: string; orders: number; filled: number; rejected: number; fees: number; signups: number }[];
  top_instruments: { symbol: string; name: string; fills: number }[];
  ledger_flows: { type: string; amount: number; count: number }[];
}
interface Log { id: string; actor_email: string | null; action: string; target_type: string | null; target_id: string | null; details: Record<string, unknown>; ip: string | null; created_at: string }

const tt = { background: "#121722", border: "1px solid #273043", borderRadius: 10, fontSize: 12 };

export default function AdminReports() {
  const [days, setDays] = useState("30");
  const [tab, setTab] = useState<"analytics" | "audit">("analytics");
  const [action, setAction] = useState("");
  const { data } = useApi<Reports>(`/api/admin/reports?days=${days}`);
  const { data: logs } = useApi<{ logs: Log[] }>(tab === "audit" ? `/api/admin/audit-logs?limit=300${action ? `&action=${encodeURIComponent(action)}` : ""}` : null);
  return (
    <div className="space-y-5">
      <PageHeader title="Reports & audit" subtitle="Platform analytics and the immutable audit trail." actions={<Segmented value={tab} onChange={setTab} options={[{ value: "analytics", label: "Analytics" }, { value: "audit", label: "Audit log" }]} />} />
      {tab === "analytics" ? (
        <>
          <Segmented size="sm" value={days} onChange={setDays} options={[{ value: "7", label: "7D" }, { value: "30", label: "30D" }, { value: "90", label: "90D" }]} />
          <div className="grid gap-5 lg:grid-cols-2">
            <Card title="Orders per day">
              <div className="h-56">{!data ? <Skeleton className="h-full" /> : (
                <ResponsiveContainer width="100%" height="100%"><BarChart data={data.daily} margin={{ left: -18, right: 0, top: 4, bottom: 0 }}>
                  <XAxis dataKey="date" tickFormatter={(d) => d.slice(5)} tick={{ fill: "#566173", fontSize: 10 }} axisLine={false} tickLine={false} minTickGap={16} />
                  <YAxis tick={{ fill: "#566173", fontSize: 10 }} axisLine={false} tickLine={false} allowDecimals={false} />
                  <Tooltip cursor={{ fill: "#1D2433" }} contentStyle={tt} />
                  <Bar dataKey="filled" stackId="a" fill="#22C993" name="Filled" />
                  <Bar dataKey="rejected" stackId="a" fill="#F2595F" name="Rejected" radius={[3, 3, 0, 0]} />
                </BarChart></ResponsiveContainer>)}
              </div>
            </Card>
            <Card title="Simulated fee revenue & sign-ups">
              <div className="h-56">{!data ? <Skeleton className="h-full" /> : (
                <ResponsiveContainer width="100%" height="100%"><LineChart data={data.daily} margin={{ left: -6, right: 0, top: 4, bottom: 0 }}>
                  <XAxis dataKey="date" tickFormatter={(d) => d.slice(5)} tick={{ fill: "#566173", fontSize: 10 }} axisLine={false} tickLine={false} minTickGap={16} />
                  <YAxis yAxisId="f" tick={{ fill: "#566173", fontSize: 10 }} axisLine={false} tickLine={false} />
                  <YAxis yAxisId="s" orientation="right" tick={{ fill: "#566173", fontSize: 10 }} axisLine={false} tickLine={false} allowDecimals={false} />
                  <Tooltip contentStyle={tt} formatter={(v: number, n: string) => (n === "Fees" ? inr(v) : v)} />
                  <Line yAxisId="f" dataKey="fees" name="Fees" stroke="#8C9BFF" strokeWidth={2} dot={false} />
                  <Line yAxisId="s" dataKey="signups" name="Sign-ups" stroke="#F2B54A" strokeWidth={2} dot={false} />
                </LineChart></ResponsiveContainer>)}
              </div>
            </Card>
          </div>
          <div className="grid gap-5 lg:grid-cols-2">
            <Card title="Most traded instruments">
              {!data ? <Skeleton className="h-48" /> : (
                <Table><thead><tr><Th>Instrument</Th><Th align="right">Fills</Th></tr></thead><tbody>
                  {data.top_instruments.map((t) => <tr key={t.symbol}><Td><div className="font-medium text-fg">{t.name}</div><div className="text-xs text-fg-muted">{t.symbol}</div></Td><Td align="right">{t.fills}</Td></tr>)}
                </tbody></Table>
              )}
            </Card>
            <Card title="Ledger flows">
              {!data ? <Skeleton className="h-48" /> : (
                <Table><thead><tr><Th>Type</Th><Th align="right">Entries</Th><Th align="right">Net amount</Th></tr></thead><tbody>
                  {data.ledger_flows.map((f) => <tr key={f.type}><Td className="text-xs font-semibold uppercase tracking-wide text-fg-soft">{f.type.replace(/_/g, " ")}</Td><Td align="right">{f.count}</Td><Td align="right">{inr(f.amount, { sign: true })}</Td></tr>)}
                </tbody></Table>
              )}
            </Card>
          </div>
        </>
      ) : (
        <Card>
          <div className="mb-4 sm:w-72"><Input placeholder="Filter by action prefix, e.g. admin." value={action} onChange={(e) => setAction(e.target.value)} aria-label="Filter actions" /></div>
          {!logs ? <Skeleton className="h-96" /> : (
            <Table>
              <thead><tr><Th>Time</Th><Th>Actor</Th><Th>Action</Th><Th>Target</Th><Th>Details</Th><Th>IP</Th></tr></thead>
              <tbody>
                {logs.logs.map((l) => (
                  <tr key={l.id} className="align-top hover:bg-ink-800/60">
                    <Td className="text-fg-muted">{dateTime(l.created_at)}</Td>
                    <Td className="text-fg-soft">{l.actor_email ?? "—"}</Td>
                    <Td className="font-mono text-xs text-brand">{l.action}</Td>
                    <Td className="text-xs text-fg-muted">{l.target_type ? `${l.target_type}:${(l.target_id ?? "").slice(0, 14)}` : "—"}</Td>
                    <Td className="max-w-[360px] truncate font-mono text-[11px] text-fg-muted" >{Object.keys(l.details ?? {}).length ? JSON.stringify(l.details) : "—"}</Td>
                    <Td className="text-xs text-fg-faint">{l.ip ?? "—"}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
      )}
    </div>
  );
}
