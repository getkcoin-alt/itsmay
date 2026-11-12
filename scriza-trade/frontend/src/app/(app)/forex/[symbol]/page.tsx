"use client";

import { useParams } from "next/navigation";
import { InstrumentDetail } from "@/components/market/instrument-detail";

export default function ForexDetailPage() {
  const { symbol } = useParams<{ symbol: string }>();
  return <InstrumentDetail symbol={decodeURIComponent(symbol).toUpperCase()} backHref="/forex" backLabel="All forex" />;
}
