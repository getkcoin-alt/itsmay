"use client";

import { useState } from "react";
import { OrdersTable } from "@/components/trade/tables";
import { Button, Card, Empty, LinkButton, Modal, PageHeader, Segmented, SimBadge, Skeleton } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api, refreshAll, useApi } from "@/lib/api";
import { price, qty } from "@/lib/format";
import type { Order } from "@/lib/types";

type Tab = "ALL" | "OPEN" | "FILLED" | "REJECTED" | "CANCELLED";

export default function OrdersPage() {
  const [tab, setTab] = useState<Tab>("ALL");
  const [cls, setCls] = useState<string>("");
  const params = new URLSearchParams();
  if (tab !== "ALL") params.set("status", tab);
  if (cls) params.set("asset_class", cls);
  const { data, mutate } = useApi<{ orders: Order[]; counts: Record<string, number> }>(`/api/orders?${params}`, { refreshInterval: 5000 });
  const [cancelling, setCancelling] = useState<Order | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const c = data?.counts ?? {};

  const doCancel = async () => {
    if (!cancelling) return;
    setBusy(true);
    try {
      await api(`/api/orders/${cancelling.id}`, { method: "DELETE" });
      toast({ kind: "success", title: "Order cancelled", body: "Reserved funds have been released." });
      setCancelling(null);
      await mutate();
      refreshAll();
    } catch (e) {
      toast({ kind: "error", title: "Cancel failed", body: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      <PageHeader title="Orders" subtitle="Your simulated order book: open limit orders, executions and rejections." actions={<SimBadge />} />
      <Card>
        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="-mx-1 overflow-x-auto px-1">
            <Segmented
              value={tab}
              onChange={setTab}
              options={[
                { value: "ALL", label: "All" },
                { value: "OPEN", label: `Open${c.OPEN ? ` · ${c.OPEN}` : ""}` },
                { value: "FILLED", label: `Executed${c.FILLED ? ` · ${c.FILLED}` : ""}` },
                { value: "REJECTED", label: `Rejected${c.REJECTED ? ` · ${c.REJECTED}` : ""}` },
                { value: "CANCELLED", label: "Cancelled" },
              ]}
            />
          </div>
          <select value={cls} onChange={(e) => setCls(e.target.value)} className="h-9 rounded-lg border border-ink-600 bg-ink-900 px-3 text-sm text-fg" aria-label="Asset class">
            <option value="">All asset classes</option>
            <option value="stock">Stocks</option>
            <option value="option">Options</option>
            <option value="crypto">Crypto</option>
            <option value="forex">Forex</option>
          </select>
        </div>
        {!data ? <Skeleton className="h-72" /> : data.orders.length ? <OrdersTable orders={data.orders} onCancel={setCancelling} /> : (
          <Empty title="No orders in this view" action={<LinkButton href="/markets" size="sm" variant="primary">Place an order</LinkButton>} />
        )}
      </Card>
      <Modal
        open={!!cancelling}
        onClose={() => setCancelling(null)}
        title="Cancel open order?"
        footer={
          <div className="flex gap-2">
            <Button variant="ghost" className="flex-1" onClick={() => setCancelling(null)}>Keep order</Button>
            <Button variant="danger" className="flex-1" loading={busy} onClick={doCancel}>Cancel order</Button>
          </div>
        }
      >
        {cancelling && (
          <p className="text-sm text-fg-soft">
            {cancelling.side} {qty(cancelling.lots ?? cancelling.quantity)} {cancelling.lots ? "lot(s)" : ""} {cancelling.symbol} @ limit {price(cancelling.limit_price, 2)}. Any reserved funds will be released immediately.
          </p>
        )}
      </Modal>
    </div>
  );
}
