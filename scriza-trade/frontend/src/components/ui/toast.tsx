"use client";

import { CheckCircle2, AlertTriangle, Info, X, XCircle } from "lucide-react";
import { createContext, type ReactNode, useCallback, useContext, useState } from "react";
import { cx } from "./index";

type Kind = "success" | "error" | "info" | "warning";
interface ToastItem {
  id: number;
  kind: Kind;
  title: string;
  body?: ReactNode;
}

const Ctx = createContext<(t: Omit<ToastItem, "id">) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const push = useCallback((t: Omit<ToastItem, "id">) => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs.slice(-3), { ...t, id }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), t.kind === "error" ? 7000 : 5000);
  }, []);
  const icons = { success: CheckCircle2, error: XCircle, info: Info, warning: AlertTriangle };
  const colors = { success: "text-up", error: "text-down", info: "text-brand", warning: "text-warn" };
  return (
    <Ctx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 top-3 z-[60] flex flex-col items-center gap-2 px-3 sm:bottom-6 sm:left-auto sm:right-6 sm:top-auto sm:items-end">
        {items.map((t) => {
          const Icon = icons[t.kind];
          return (
            <div key={t.id} role="status" className="pointer-events-auto flex w-full max-w-sm animate-rise items-start gap-3 rounded-xl border border-ink-600 bg-ink-800/95 p-3.5 shadow-pop backdrop-blur">
              <Icon size={18} className={cx("mt-0.5 shrink-0", colors[t.kind])} />
              <div className="min-w-0 flex-1">
                <div className="text-sm font-semibold text-fg">{t.title}</div>
                {t.body && <div className="mt-0.5 text-[13px] text-fg-soft">{t.body}</div>}
              </div>
              <button className="text-fg-muted hover:text-fg" onClick={() => setItems((xs) => xs.filter((x) => x.id !== t.id))} aria-label="Dismiss">
                <X size={15} />
              </button>
            </div>
          );
        })}
      </div>
    </Ctx.Provider>
  );
}

export const useToast = () => useContext(Ctx);
