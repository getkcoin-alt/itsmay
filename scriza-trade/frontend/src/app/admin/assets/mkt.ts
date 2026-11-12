import { api } from "@/lib/api";

export function mkt(symbol: string, body: Record<string, unknown>) {
  return api(`/api/admin/assets/${symbol}`, { method: "PATCH", json: body });
}
