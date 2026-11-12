"use client";

import { useState } from "react";
import { OrdersTable, PositionsTable } from "@/components/trade/tables";
import { Button, Card, Input, Modal, PageHeader, Skeleton, Stat, StatusBadge, Table, Td, Th } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api, useApi } from "@/lib/api";
import { dateTime, inr, tone } from "@/lib/format";
import type { Order, PositionRow, Summary, User } from "@/lib/types";

type AdminUser = User & { cash_balance: number; orders: number; open_positions: number; is_seed: boolean };

export default function AdminUsers() {
  const [q, setQ] = useState("");
  const { data, mutate } = useApi<{ users: AdminUser[] }>(`/api/admin/users${q ? `?q=${encodeURIComponent(q)}` : ""}`);
  const [detail, setDetail] = useState<string | null>(null);
  const toast = useToast();
  const setStatus = async (u: AdminUser, status: "active" | "disabled") => {
    try {
      await api(`/api/admin/users/${u.id}`, { method: "PATCH", json: { status } });
      toast({ kind: "success", title: `${u.email} ${status === "active" ? "enabled" : "disabled"}`, body: status === "disabled" ? "Active sessions were revoked." : undefined });
      mutate();
    } catch (e) {
      toast({ kind: "error", title: "Update failed", body: (e as Error).message });
    }
  };
  return (
    <div className="space-y-5">
      <PageHeader title="Users" subtitle="Manage demo users and inspect their simulated accounts." />
      <Card>
        <div className="mb-4 sm:w-80"><Input placeholder="Search name or email…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search users" /></div>
        {!data ? <Skeleton className="h-80" /> : (
          <Table>
            <thead><tr><Th>User</Th><Th>Role</Th><Th>Status</Th><Th>KYC</Th><Th align="right">Cash</Th><Th align="right">Orders</Th><Th align="right">Positions</Th><Th>Last seen</Th><Th align="right" /></tr></thead>
            <tbody>
              {data.users.map((u) => (
                <tr key={u.id} className="hover:bg-ink-800/60">
                  <Td><div className="font-medium text-fg">{u.full_name}</div><div className="text-xs text-fg-muted">{u.email}</div></Td>
                  <Td className="capitalize text-fg-soft">{u.role}</Td>
                  <Td><StatusBadge status={u.status} /></Td>
                  <Td><StatusBadge status={u.kyc.status} /></Td>
                  <Td align="right">{inr(u.cash_balance)}</Td>
                  <Td align="right">{u.orders}</Td>
                  <Td align="right">{u.open_positions}</Td>
                  <Td className="text-fg-muted">{dateTime(u.last_seen_at)}</Td>
                  <Td align="right">
                    <div className="flex justify-end gap-2">
                      <Button size="sm" onClick={() => setDetail(u.id)}>View</Button>
                      {u.role !== "admin" && (u.status === "active"
                        ? <Button size="sm" variant="danger" onClick={() => setStatus(u, "disabled")}>Disable</Button>
                        : <Button size="sm" variant="secondary" onClick={() => setStatus(u, "active")}>Enable</Button>)}
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
      <UserDetail id={detail} onClose={() => setDetail(null)} onChanged={mutate} />
    </div>
  );
}

function UserDetail({ id, onClose, onChanged }: { id: string | null; onClose: () => void; onChanged: () => void }) {
  const { data, mutate } = useApi<{ user: User; summary: Summary; positions: PositionRow[]; orders: Order[] }>(id ? `/api/admin/users/${id}` : null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const reset = async () => {
    if (!id) return;
    setBusy(true);
    try {
      await api(`/api/admin/users/${id}/reset`, { method: "POST" });
      toast({ kind: "success", title: "Account reset to ₹10,00,000 demo cash" });
      mutate();
      onChanged();
    } catch (e) {
      toast({ kind: "error", title: "Reset failed", body: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open={!!id} onClose={onClose} wide title={data ? data.user.full_name : "Loading…"} footer={data && data.user.role !== "admin" ? <Button variant="danger" loading={busy} onClick={reset}>Reset this demo account</Button> : undefined}>
      {!data ? <Skeleton className="h-60" /> : (
        <div className="space-y-5">
          <div className="text-sm text-fg-muted">{data.user.email} · joined {dateTime(data.user.created_at)}</div>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat label="Account value" value={inr(data.summary.total_value)} valueClass="!text-base" />
            <Stat label="Cash" value={inr(data.summary.cash_balance)} valueClass="!text-base" />
            <Stat label="Overall P&L" value={inr(data.summary.overall_pnl, { sign: true })} valueClass={`!text-base ${tone(data.summary.overall_pnl)}`} />
            <Stat label="Margin" value={inr(data.summary.margin_blocked)} valueClass="!text-base" />
          </div>
          <div>
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-[0.1em] text-fg-muted">Open positions</h4>
            {data.positions.length ? <PositionsTable rows={data.positions} showClose={false} /> : <p className="text-sm text-fg-muted">None</p>}
          </div>
          <div>
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-[0.1em] text-fg-muted">Recent orders</h4>
            {data.orders.length ? <OrdersTable orders={data.orders} /> : <p className="text-sm text-fg-muted">None</p>}
          </div>
        </div>
      )}
    </Modal>
  );
}
