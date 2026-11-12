"use client";

import { Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { useApi } from "@/lib/api";
import { ASSET_LABEL, assetHref, price } from "@/lib/format";
import type { Quote } from "@/lib/types";
import { AssetIcon, Change, cx } from "../ui";

export function SearchPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [idx, setIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();
  const { data } = useApi<{ instruments: Quote[] }>(open ? "/api/market/instruments" : null);

  const results = useMemo(() => {
    const all = data?.instruments ?? [];
    const n = q.trim().toLowerCase();
    const list = n ? all.filter((i) => i.symbol.toLowerCase().includes(n) || i.name.toLowerCase().includes(n)) : all.filter((i) => i.asset_class !== "index").slice(0, 8);
    return list.slice(0, 10);
  }, [data, q]);

  useEffect(() => {
    if (open) {
      setQ("");
      setIdx(0);
      setTimeout(() => inputRef.current?.focus(), 10);
    }
  }, [open]);

  if (!open) return null;
  const go = (i: Quote) => {
    onClose();
    router.push(assetHref(i.symbol, i.asset_class));
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center p-3 pt-[10vh]" role="dialog" aria-modal="true" aria-label="Search instruments">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative w-full max-w-xl animate-rise overflow-hidden rounded-2xl border border-ink-600 bg-ink-850 shadow-pop">
        <div className="flex items-center gap-3 border-b border-ink-700 px-4">
          <Search size={18} className="text-fg-muted" />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setIdx(0);
            }}
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
              if (e.key === "ArrowDown") setIdx((i) => Math.min(i + 1, results.length - 1));
              if (e.key === "ArrowUp") setIdx((i) => Math.max(i - 1, 0));
              if (e.key === "Enter" && results[idx]) go(results[idx]);
            }}
            placeholder="Search by name or symbol — e.g. Reliance, BTC, USDINR"
            className="h-14 w-full bg-transparent text-[15px] text-fg placeholder:text-fg-faint focus:outline-none"
          />
          <kbd className="rounded border border-ink-600 px-1.5 text-[10px] text-fg-muted">ESC</kbd>
        </div>
        <ul className="max-h-[60vh] overflow-y-auto py-2">
          {results.map((r, i) => (
            <li key={r.symbol}>
              <button onMouseEnter={() => setIdx(i)} onClick={() => go(r)} className={cx("flex w-full items-center gap-3 px-4 py-2.5 text-left", i === idx && "bg-ink-750")}>
                <AssetIcon symbol={r.symbol} assetClass={r.asset_class} size={30} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 text-sm font-semibold text-fg">
                    {r.symbol}
                    <span className="rounded bg-ink-700 px-1.5 text-[10px] font-medium uppercase text-fg-muted">{ASSET_LABEL[r.asset_class]}</span>
                  </div>
                  <div className="truncate text-xs text-fg-muted">{r.name}</div>
                </div>
                <div className="text-right">
                  <div className="num text-sm text-fg">{price(r.ltp, r.decimals, r.currency)}</div>
                  <Change percent={r.change_pct} showAbs={false} className="text-xs" />
                </div>
              </button>
            </li>
          ))}
          {data && results.length === 0 && <li className="px-4 py-6 text-center text-sm text-fg-muted">No instruments match “{q}”.</li>}
          {!data && <li className="px-4 py-6 text-center text-sm text-fg-muted">Loading simulated instruments…</li>}
        </ul>
      </div>
    </div>
  );
}
