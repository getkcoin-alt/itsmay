"use client";

import { createContext, useContext } from "react";
import { useApi } from "@/lib/api";
import type { User } from "@/lib/types";

interface SessionValue {
  user: User | undefined;
  loading: boolean;
  refresh: () => void;
}

const Ctx = createContext<SessionValue>({ user: undefined, loading: true, refresh: () => {} });

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const { data, isLoading, mutate } = useApi<{ user: User }>("/api/auth/me", { revalidateOnFocus: false });
  return <Ctx.Provider value={{ user: data?.user, loading: isLoading, refresh: () => mutate() }}>{children}</Ctx.Provider>;
}

export const useSession = () => useContext(Ctx);
