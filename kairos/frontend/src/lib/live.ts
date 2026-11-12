"use client";

import { useEffect, useRef, useState } from "react";

export interface LiveTick {
  ltp: number;
  change: number;
  change_pct: number;
  bid?: number;
  ask?: number;
  high?: number;
  low?: number;
  volume?: number;
}

/**
 * Subscribes to simulated quote updates over WebSocket (/api/market/ws).
 * If the socket cannot be established (e.g. serverless hosting) callers keep
 * their SWR polling, so the UI still updates — just less frequently.
 */
export function useLiveQuotes(symbols: string[]): { ticks: Record<string, LiveTick>; connected: boolean } {
  const [ticks, setTicks] = useState<Record<string, LiveTick>>({});
  const [connected, setConnected] = useState(false);
  const key = symbols.slice().sort().join(",");
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!key) return;
    let closed = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let attempts = 0;
    const url = (process.env.NEXT_PUBLIC_WS_URL || `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}`) + "/api/market/ws";

    const connect = () => {
      if (closed || attempts > 4) return;
      attempts += 1;
      let ws: WebSocket;
      try {
        ws = new WebSocket(url);
      } catch {
        return;
      }
      wsRef.current = ws;
      ws.onopen = () => {
        attempts = 0;
        setConnected(true);
        ws.send(JSON.stringify({ subscribe: key.split(",") }));
      };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === "quotes") setTicks((prev) => ({ ...prev, ...msg.data }));
        } catch {
          /* ignore malformed frame */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry = setTimeout(connect, 1500 * attempts);
      };
      ws.onerror = () => ws.close();
    };
    connect();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      wsRef.current?.close();
    };
  }, [key]);

  return { ticks, connected };
}

/** Returns "up" / "down" for a short time whenever the value changes. */
export function useFlash(value: number | undefined): "up" | "down" | null {
  const prev = useRef<number | undefined>(value);
  const [dir, setDir] = useState<"up" | "down" | null>(null);
  useEffect(() => {
    if (value === undefined || prev.current === undefined) {
      prev.current = value;
      return;
    }
    if (value !== prev.current) {
      setDir(value > prev.current ? "up" : "down");
      prev.current = value;
      const t = setTimeout(() => setDir(null), 900);
      return () => clearTimeout(t);
    }
  }, [value]);
  return dir;
}
