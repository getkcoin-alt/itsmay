"use client";

import { SWRConfig } from "swr";
import { ToastProvider } from "@/components/ui/toast";
import { fetcher } from "@/lib/api";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <SWRConfig value={{ fetcher, shouldRetryOnError: false, dedupingInterval: 1500 }}>
      <ToastProvider>{children}</ToastProvider>
    </SWRConfig>
  );
}
