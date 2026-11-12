"use client";

import { useState } from "react";
import { OrdersTable } from "@/components/trade/tables";
import { Card, Input, PageHeader, Segmented, Skeleton, Empty } from "@/components/ui";
import { useApi } from "@/lib/api";
import type { Order } from "@/lib/types";

export default function AdminOrders() {
  const [status, setStatus] = useState("");
  const [cls, setCls] = useState("");
  const [q, setQ] = useState("");
  const p = new URLSearchParams();
  if (status) p.set("status", status);
  if (cls) p.set("asset_class", cls);
  if (q) p.set("q", q);
  const { data } = useApi<{ orders: Order[] }>(`/api/admin/orders?${p}`, { refreshInterval: 8000 });
  return (
    <div className="space-y-5">
      <PageHeader title="Orders" subtitle="All simulated orders across users." />
      <Card>
        <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center">
          <Segmented size="sm" value={status} onChange={setStatus} options={[{ value: "", label: "All" }, { value: "OPEN", label: "Open" }, { value: "FILLED", label: "Filled" }, { value: "REJECTED", label: "Rejected" }, { value: "CANCELLED", label: "Cancelled" }]} />
          <Segmented size="sm" value={cls} onChange={setCls} options={[{ value: "", label: "Any class" }, { value: "stock", label: "Stocks" }, { value: "option", label: "Options" }, { value: "crypto", label: "Crypto" }, { value: "forex", label: "Forex" }]} />
          <div className="lg:ml-auto lg:w-72"><Input placeholder="Symbol or user email…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search orders" /></div>
        </div>
        {!data ? <Skeleton className="h-96" /> : data.orders.length ? <OrdersTable orders={data.orders} showUser /> : <Empty title="No matching orders" />}
      </Card>
    </div>
  );
}
