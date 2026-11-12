"use client";

import { AreaChart, CandlestickChart } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useApi } from "@/lib/api";
import type { Candle } from "@/lib/types";
import { cx, Segmented, Skeleton } from "../ui";

const IST_OFFSET = 19800; // charts render in exchange time (IST)
const RANGES = ["1D", "1W", "1M", "3M", "1Y", "5Y"] as const;
type Range = (typeof RANGES)[number];

export function PriceChart({ symbol, decimals = 2, height = 360, live, option = false }: { symbol: string; decimals?: number; height?: number; live?: number; option?: boolean }) {
  const [range, setRange] = useState<Range>(option ? "1D" : "1M");
  const [mode, setMode] = useState<"candle" | "area">("area");
  const { data, isLoading } = useApi<{ candles: Candle[]; interval: string }>(`/api/market/candles/${encodeURIComponent(symbol)}?range=${range}`, {
    refreshInterval: range === "1D" ? 15000 : 60000,
  });
  const box = useRef<HTMLDivElement>(null);
  const chartRef = useRef<{ update: (price: number) => void } | null>(null);

  useEffect(() => {
    if (!box.current || !data?.candles?.length) return;
    let disposed = false;
    let cleanup = () => {};
    (async () => {
      const lw = await import("lightweight-charts");
      if (disposed || !box.current) return;
      const el = box.current;
      const chart = lw.createChart(el, {
        width: el.clientWidth,
        height,
        layout: { background: { color: "transparent" }, textColor: "#7C8899", fontFamily: "var(--font-sans)", fontSize: 11 },
        grid: { vertLines: { color: "rgba(39,48,67,0.35)" }, horzLines: { color: "rgba(39,48,67,0.35)" } },
        rightPriceScale: { borderColor: "#1D2433", scaleMargins: { top: 0.1, bottom: 0.22 } },
        timeScale: { borderColor: "#1D2433", timeVisible: range === "1D" || range === "1W" || option, secondsVisible: false },
        crosshair: { mode: lw.CrosshairMode.Normal, vertLine: { color: "#566173", labelBackgroundColor: "#273043" }, horzLine: { color: "#566173", labelBackgroundColor: "#273043" } },
        localization: { priceFormatter: (p: number) => p.toLocaleString("en-IN", { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) },
        handleScroll: { vertTouchDrag: false },
      });
      const candles = data.candles.map((c) => ({ ...c, time: (c.time + IST_OFFSET) as import("lightweight-charts").UTCTimestamp }));
      const first = candles[0].open;
      const last = candles[candles.length - 1].close;
      const upTrend = last >= first;
      const lineColor = upTrend ? "#22C993" : "#F2595F";
      const minMove = Math.pow(10, -decimals);
      let series: import("lightweight-charts").ISeriesApi<"Candlestick"> | import("lightweight-charts").ISeriesApi<"Area">;
      if (mode === "candle") {
        series = chart.addCandlestickSeries({
          upColor: "#22C993", downColor: "#F2595F", borderUpColor: "#22C993", borderDownColor: "#F2595F", wickUpColor: "#22C99399", wickDownColor: "#F2595F99",
          priceFormat: { type: "price", precision: decimals, minMove },
        });
        series.setData(candles);
      } else {
        series = chart.addAreaSeries({
          lineColor, lineWidth: 2, topColor: `${lineColor}33`, bottomColor: `${lineColor}00`,
          priceFormat: { type: "price", precision: decimals, minMove }, crosshairMarkerRadius: 4,
        });
        series.setData(candles.map((c) => ({ time: c.time, value: c.close })));
      }
      if (!option && candles.some((c) => c.volume > 0)) {
        const vol = chart.addHistogramSeries({ priceScaleId: "", priceFormat: { type: "volume" }, color: "#36415A" });
        vol.priceScale().applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
        vol.setData(candles.map((c) => ({ time: c.time, value: c.volume, color: c.close >= c.open ? "#22C99338" : "#F2595F38" })));
      }
      chart.timeScale().fitContent();
      const lastBar = candles[candles.length - 1];
      chartRef.current = {
        update: (p: number) => {
          if (mode === "candle") {
            (series as import("lightweight-charts").ISeriesApi<"Candlestick">).update({ ...lastBar, close: p, high: Math.max(lastBar.high, p), low: Math.min(lastBar.low, p) });
          } else {
            (series as import("lightweight-charts").ISeriesApi<"Area">).update({ time: lastBar.time, value: p });
          }
        },
      };
      const ro = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth }));
      ro.observe(el);
      cleanup = () => {
        ro.disconnect();
        chart.remove();
        chartRef.current = null;
      };
    })();
    return () => {
      disposed = true;
      cleanup();
    };
  }, [data, mode, height, decimals, range, option]);

  useEffect(() => {
    if (live && chartRef.current) chartRef.current.update(live);
  }, [live]);

  return (
    <div>
      <div className="mb-3 flex items-center justify-between gap-2">
        <Segmented size="sm" value={range} onChange={setRange} options={(option ? (["1D", "1W"] as Range[]) : RANGES).map((r) => ({ value: r, label: r }))} />
        <div className="flex items-center gap-2">
          <span className="hidden text-[10px] font-semibold uppercase tracking-[0.12em] text-warn sm:inline">Simulated</span>
          <div className="flex rounded-lg border border-ink-700 bg-ink-900 p-0.5">
            {(["area", "candle"] as const).map((m) => (
              <button key={m} onClick={() => setMode(m)} aria-label={m === "area" ? "Line chart" : "Candlestick chart"} className={cx("rounded-md p-1.5", mode === m ? "bg-ink-700 text-fg" : "text-fg-muted hover:text-fg")}>
                {m === "area" ? <AreaChart size={15} /> : <CandlestickChart size={15} />}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="relative" style={{ height }}>
        {isLoading && !data && <Skeleton className="absolute inset-0" />}
        <div ref={box} className="absolute inset-0" />
      </div>
    </div>
  );
}
