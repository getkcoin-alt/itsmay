"use client";

import { IndexStrip } from "@/components/market/index-strip";
import { InstrumentTable } from "@/components/market/instrument-list";
import { Card, PageHeader, SimBadge } from "@/components/ui";
import { useApi } from "@/lib/api";
import type { Quote } from "@/lib/types";

export default function StocksPage() {
  const { data } = useApi<{ indices: Quote[] }>("/api/market/overview", { refreshInterval: 8000 });
  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Equities" title="Stocks" subtitle="Discover and trade NSE-listed names in the simulated cash market. Delivery product, no short selling." actions={<SimBadge />} />
      <IndexStrip indices={data?.indices} />
      <Card>
        <InstrumentTable assetClass="stock" title="stocks" />
      </Card>
    </div>
  );
}
