"use client";

import Link from "next/link";
import { useLiveQuotes } from "@/lib/live";
import { price } from "@/lib/format";
import type { Quote } from "@/lib/types";
import { Badge, Change, Flash, Skeleton } from "../ui";
import { Sparkline } from "./instrument-list";

export function IndexStrip({ indices }: { indices?: Quote[] }) {
  const { ticks } = useLiveQuotes((indices ?? []).map((i) => i.symbol));
  if (!indices) return <Skeleton className="h-[74px] rounded-2xl" />;
  return (
    <div className="-mx-4 flex snap-x gap-3 overflow-x-auto px-4 pb-1 sm:mx-0 sm:grid sm:grid-cols-3 sm:px-0 xl:grid-cols-5">
      {indices.map((i0) => {
        const i = { ...i0, ...(ticks[i0.symbol] ?? {}) };
        return (
          <Link
            key={i.symbol}
            href={i.has_options ? `/options?underlying=${i.symbol}` : "/markets"}
            className="flex min-w-[200px] snap-start items-center justify-between gap-3 rounded-2xl border border-ink-700/80 bg-ink-850 px-4 py-3 transition-colors hover:border-ink-600"
          >
            <div className="min-w-0">
              <div className="flex items-center gap-1.5 text-xs font-semibold text-fg-soft">
                {i.name}
                {i.has_options && <Badge tone="brand" className="!px-1 !py-0 text-[9px]">F&O</Badge>}
              </div>
              <div className="num mt-0.5 text-[15px] font-semibold text-fg"><Flash value={i.ltp}>{price(i.ltp, 2)}</Flash></div>
              <Change value={i.change} percent={i.change_pct} className="text-[11px]" />
            </div>
            <Sparkline symbol={i.symbol} positive={i.change >= 0} width={64} height={30} />
          </Link>
        );
      })}
    </div>
  );
}

