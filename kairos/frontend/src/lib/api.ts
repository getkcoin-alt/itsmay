"use client";

import useSWR, { type SWRConfiguration, mutate as globalMutate } from "swr";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function messageFrom(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d) && d.length) {
      const first = d[0] as { msg?: string; loc?: string[] };
      const field = first.loc?.slice(-1)[0];
      return `${field ? field.replace(/_/g, " ") + ": " : ""}${(first.msg || fallback).replace(/^Value error, /, "")}`;
    }
  }
  return fallback;
}

export async function api<T = unknown>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  const res = await fetch(path, {
    credentials: "same-origin",
    ...rest,
    headers: {
      "x-kairos-client": "web",
      ...(json !== undefined ? { "content-type": "application/json" } : {}),
      ...headers,
    },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) {
    if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/api/auth/login")) {
      // Session expired or revoked: clear the cookie, then go to login.
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      await fetch("/api/auth/logout", { method: "POST", headers: { "x-kairos-client": "web" } }).catch(() => {});
      if (!window.location.pathname.startsWith("/login")) window.location.href = `/login?next=${next}`;
    }
    throw new ApiError(res.status, messageFrom(body, `Request failed (${res.status})`));
  }
  return body as T;
}

export const fetcher = <T,>(path: string) => api<T>(path);

export function useApi<T>(path: string | null, config?: SWRConfiguration<T>) {
  return useSWR<T>(path, fetcher, { revalidateOnFocus: true, keepPreviousData: true, ...config });
}

/** Revalidate every cached query whose key starts with one of the prefixes. */
export function refreshAll(prefixes: string[] = ["/api/"]) {
  return globalMutate((key) => typeof key === "string" && prefixes.some((p) => key.startsWith(p)));
}

/** Idempotency key for order submission (works outside secure contexts too). */
export function uid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto && typeof crypto.randomUUID === "function" && window.isSecureContext) return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}
