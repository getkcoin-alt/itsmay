const inrFmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
const intFmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export const CCY_SYMBOL: Record<string, string> = { INR: "₹", USD: "$", JPY: "¥", CHF: "CHF ", CAD: "C$", EUR: "€", GBP: "£" };

/** ₹10,00,000.00 — Indian digit grouping. */
export function inr(value: number | null | undefined, opts: { sign?: boolean; decimals?: number } = {}): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const d = opts.decimals ?? 2;
  const f = new Intl.NumberFormat("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d });
  const s = f.format(Math.abs(value));
  const sign = value < 0 ? "−" : opts.sign && value > 0 ? "+" : "";
  return `${sign}₹${s}`;
}

/** Compact Indian notation: ₹12.4 L, ₹3.2 Cr. */
export function inrCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const a = Math.abs(value);
  const sign = value < 0 ? "−" : "";
  if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(2)} Cr`;
  if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(2)} L`;
  if (a >= 1e3) return `${sign}₹${(a / 1e3).toFixed(1)}K`;
  return `${sign}₹${a.toFixed(0)}`;
}

export function price(value: number | null | undefined, decimals = 2, currency?: string): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const f = new Intl.NumberFormat(currency === "INR" || !currency ? "en-IN" : "en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  return `${currency ? CCY_SYMBOL[currency] ?? "" : ""}${f.format(value)}`;
}

export function pct(value: number | null | undefined, decimals = 2, sign = true): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const s = Math.abs(value).toFixed(decimals);
  return `${value < 0 ? "−" : sign && value > 0 ? "+" : ""}${s}%`;
}

export function signed(value: number, decimals = 2): string {
  const s = new Intl.NumberFormat("en-IN", { minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(Math.abs(value));
  return `${value < 0 ? "−" : value > 0 ? "+" : ""}${s}`;
}

export function qty(value: number, maxDecimals = 6): string {
  return new Intl.NumberFormat("en-IN", { maximumFractionDigits: maxDecimals }).format(value);
}

export function compact(value: number): string {
  const a = Math.abs(value);
  if (a >= 1e7) return `${(value / 1e7).toFixed(2)} Cr`;
  if (a >= 1e5) return `${(value / 1e5).toFixed(2)} L`;
  if (a >= 1e3) return `${(value / 1e3).toFixed(1)}K`;
  return intFmt.format(value);
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
}

export function dateOnly(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "Asia/Kolkata" });
}

export function expiryLabel(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN", { day: "2-digit", month: "short", timeZone: "Asia/Kolkata" });
}

export function tone(value: number): string {
  return value > 0 ? "text-up" : value < 0 ? "text-down" : "text-fg-muted";
}

export const ASSET_LABEL: Record<string, string> = {
  stock: "Stocks",
  crypto: "Crypto",
  forex: "Forex",
  option: "Options",
  index: "Index",
  cash: "Free cash",
};

export const ASSET_COLOR: Record<string, string> = {
  stock: "#8C9BFF",
  crypto: "#F2B54A",
  forex: "#4FC3D9",
  option: "#C58CFF",
  cash: "#3A4558",
};

export function assetHref(symbol: string, assetClass: string): string {
  if (assetClass === "option") return `/options?symbol=${encodeURIComponent(symbol)}`;
  if (assetClass === "crypto") return `/crypto/${symbol}`;
  if (assetClass === "forex") return `/forex/${symbol}`;
  if (assetClass === "index") return `/options?underlying=${symbol}`;
  return `/stocks/${symbol}`;
}

export function inrFixed(v: number) {
  return inrFmt.format(v);
}
