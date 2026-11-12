"use client";

import { Info } from "lucide-react";
import { InstrumentTable } from "@/components/market/instrument-list";
import { Card, PageHeader, SimBadge } from "@/components/ui";

export default function ForexPage() {
  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Currencies" title="Forex" subtitle="Simulated currency pairs traded as leveraged margin positions in lots of 1,000 units of the base currency." actions={<SimBadge />} />
      <div className="flex gap-3 rounded-xl border border-brand/25 bg-brand-soft px-4 py-3 text-[13px] text-fg-soft">
        <Info size={16} className="mt-0.5 shrink-0 text-brand" />
        <p>
          Instrument availability is configurable per jurisdiction. In a production deployment for Indian residents, retail access would typically be restricted to exchange-traded INR pairs under the applicable regulations; the administrator can enforce an “INR pairs only” mode from the back office.
        </p>
      </div>
      <Card>
        <InstrumentTable assetClass="forex" title="currency pairs" />
      </Card>
    </div>
  );
}
