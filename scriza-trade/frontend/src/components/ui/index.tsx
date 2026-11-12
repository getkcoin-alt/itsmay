"use client";

import clsx from "clsx";
import { X } from "lucide-react";
import Link from "next/link";
import { type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, forwardRef, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { pct, tone } from "@/lib/format";
import { useFlash } from "@/lib/live";

export const cx = clsx;

// Buttons -------------------------------------------------------------------

type Variant = "primary" | "secondary" | "ghost" | "buy" | "sell" | "danger";
const variants: Record<Variant, string> = {
  primary: "bg-brand text-ink-950 hover:bg-[#a3afff] font-semibold",
  secondary: "bg-ink-700 text-fg hover:bg-ink-600 border border-ink-600",
  ghost: "text-fg-soft hover:text-fg hover:bg-ink-750",
  buy: "bg-up text-ink-950 hover:brightness-110 font-semibold",
  sell: "bg-down text-white hover:brightness-110 font-semibold",
  danger: "bg-down/15 text-down hover:bg-down/25 border border-down/30",
};

export const Button = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md" | "lg"; loading?: boolean }
>(function Button({ variant = "secondary", size = "md", loading, className, children, disabled, ...rest }, ref) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-lg transition-colors duration-150 disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/60 whitespace-nowrap",
        size === "sm" && "h-8 px-3 text-xs",
        size === "md" && "h-10 px-4 text-sm",
        size === "lg" && "h-12 px-5 text-[15px]",
        variants[variant],
        className,
      )}
      {...rest}
    >
      {loading && <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-r-transparent" />}
      {children}
    </button>
  );
});

export function LinkButton({ href, variant = "secondary", size = "md", className, children }: { href: string; variant?: Variant; size?: "sm" | "md" | "lg"; className?: string; children: ReactNode }) {
  return (
    <Link
      href={href}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-lg transition-colors duration-150 whitespace-nowrap",
        size === "sm" && "h-8 px-3 text-xs",
        size === "md" && "h-10 px-4 text-sm",
        size === "lg" && "h-12 px-5 text-[15px]",
        variants[variant],
        className,
      )}
    >
      {children}
    </Link>
  );
}

// Surfaces ------------------------------------------------------------------

export function Card({ className, children, title, action, padded = true, subtitle }: { className?: string; children: ReactNode; title?: ReactNode; action?: ReactNode; padded?: boolean; subtitle?: ReactNode }) {
  return (
    <section className={cx("rounded-2xl border border-ink-700/80 bg-ink-850 shadow-panel", className)}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 px-4 pt-4 sm:px-5">
          <div className="min-w-0">
            {title && <h2 className="truncate text-[13px] font-semibold uppercase tracking-[0.08em] text-fg-soft">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-fg-muted">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      <div className={cx(padded && "p-4 sm:p-5", title && padded && "pt-3 sm:pt-3")}>{children}</div>
    </section>
  );
}

export function Badge({ children, tone: t = "neutral", className }: { children: ReactNode; tone?: "neutral" | "up" | "down" | "warn" | "brand"; className?: string }) {
  const map = {
    neutral: "bg-ink-700 text-fg-soft",
    up: "bg-up-soft text-up",
    down: "bg-down-soft text-down",
    warn: "bg-warn-soft text-warn",
    brand: "bg-brand-soft text-brand",
  } as const;
  return <span className={cx("inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide", map[t], className)}>{children}</span>;
}

export function SimBadge({ className }: { className?: string }) {
  return (
    <span className={cx("hidden items-center gap-1.5 rounded-md border border-warn/30 bg-warn-soft md:inline-flex px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.12em] text-warn", className)}>
      <span className="h-1.5 w-1.5 rounded-full bg-warn" /> Demo · Simulated data
    </span>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const t = status === "FILLED" || status === "APPROVED" || status === "active" ? "up" : status === "REJECTED" || status === "disabled" ? "down" : status === "OPEN" || status === "PENDING" ? "warn" : "neutral";
  return <Badge tone={t}>{status.replace(/_/g, " ")}</Badge>;
}

// Data display --------------------------------------------------------------

export function Change({ value, percent, className, showAbs = true, decimals = 2 }: { value?: number; percent: number; className?: string; showAbs?: boolean; decimals?: number }) {
  return (
    <span className={cx("num whitespace-nowrap", tone(percent), className)}>
      {showAbs && value !== undefined && <>{value > 0 ? "+" : value < 0 ? "−" : ""}{Math.abs(value).toLocaleString("en-IN", { minimumFractionDigits: decimals, maximumFractionDigits: decimals })} </>}
      ({pct(percent)})
    </span>
  );
}

export function Flash({ value, children, className }: { value: number | undefined; children: ReactNode; className?: string }) {
  const dir = useFlash(value);
  return <span className={cx("rounded px-0.5 -mx-0.5", dir === "up" && "animate-flash-up", dir === "down" && "animate-flash-down", className)}>{children}</span>;
}

export function Stat({ label, value, sub, className, valueClass }: { label: ReactNode; value: ReactNode; sub?: ReactNode; className?: string; valueClass?: string }) {
  return (
    <div className={cx("min-w-0", className)}>
      <div className="text-[11px] font-medium uppercase tracking-[0.1em] text-fg-muted">{label}</div>
      <div className={cx("num mt-1 truncate text-lg font-semibold text-fg sm:text-xl", valueClass)}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-fg-muted">{sub}</div>}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("animate-pulse rounded-md bg-ink-700/60", className)} />;
}

export function Empty({ title, body, action, icon }: { title: string; body?: ReactNode; action?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-10 text-center">
      {icon && <div className="mb-3 text-fg-faint">{icon}</div>}
      <div className="text-sm font-semibold text-fg">{title}</div>
      {body && <div className="mt-1 max-w-sm text-sm text-fg-muted">{body}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="rounded-lg border border-down/30 bg-down-soft px-3 py-2 text-sm text-down">{error instanceof Error ? error.message : String(error)}</div>;
}

// Inputs --------------------------------------------------------------------

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { label?: string; hint?: ReactNode; suffix?: ReactNode; error?: string }>(
  function Input({ label, hint, suffix, error, className, id, ...rest }, ref) {
    const inputId = id || rest.name;
    return (
      <label className="block" htmlFor={inputId}>
        {label && <span className="mb-1.5 block text-xs font-medium text-fg-soft">{label}</span>}
        <span className={cx("flex h-11 items-center rounded-lg border bg-ink-900 px-3 transition-colors focus-within:border-brand/70", error ? "border-down/60" : "border-ink-600")}>
          <input ref={ref} id={inputId} className={cx("num h-full w-full min-w-0 bg-transparent text-[15px] text-fg placeholder:text-fg-faint focus:outline-none", className)} {...rest} />
          {suffix && <span className="ml-2 shrink-0 text-xs text-fg-muted">{suffix}</span>}
        </span>
        {error ? <span className="mt-1 block text-xs text-down">{error}</span> : hint ? <span className="mt-1 block text-xs text-fg-muted">{hint}</span> : null}
      </label>
    );
  },
);

export function Select({ label, className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement> & { label?: string }) {
  return (
    <label className="block">
      {label && <span className="mb-1.5 block text-xs font-medium text-fg-soft">{label}</span>}
      <select className={cx("h-11 w-full rounded-lg border border-ink-600 bg-ink-900 px-3 text-sm text-fg focus:border-brand/70 focus:outline-none", className)} {...rest}>
        {children}
      </select>
    </label>
  );
}

export function Segmented<T extends string>({ options, value, onChange, className, size = "md" }: { options: { value: T; label: ReactNode }[]; value: T; onChange: (v: T) => void; className?: string; size?: "sm" | "md" }) {
  return (
    <div role="tablist" className={cx("inline-flex rounded-lg border border-ink-700 bg-ink-900 p-0.5", className)}>
      {options.map((o) => (
        <button
          key={o.value}
          role="tab"
          aria-selected={value === o.value}
          onClick={() => onChange(o.value)}
          className={cx(
            "rounded-md font-medium transition-colors",
            size === "sm" ? "px-2.5 py-1 text-xs" : "px-3 py-1.5 text-[13px]",
            value === o.value ? "bg-ink-700 text-fg shadow-sm" : "text-fg-muted hover:text-fg",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label?: string; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cx("relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50", checked ? "bg-up" : "bg-ink-600")}
    >
      <span className={cx("inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform", checked ? "translate-x-5" : "translate-x-0.5")} />
    </button>
  );
}

// Overlay -------------------------------------------------------------------

export function Modal({ open, onClose, title, children, wide, footer }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; wide?: boolean; footer?: ReactNode }) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open, onClose]);
  if (!open || !mounted) return null;
  // Portal to <body> so sticky/transformed ancestors cannot trap the overlay below other content.
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center sm:items-center sm:p-4" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-[2px]" onClick={onClose} />
      <div className={cx("relative max-h-[92dvh] w-full animate-rise overflow-hidden rounded-t-2xl border border-ink-600 bg-ink-850 shadow-pop sm:rounded-2xl", wide ? "sm:max-w-2xl" : "sm:max-w-md")}>
        <div className="flex items-center justify-between border-b border-ink-700 px-5 py-4">
          <h3 className="text-base font-semibold text-fg">{title}</h3>
          <button onClick={onClose} className="rounded-md p-1 text-fg-muted hover:bg-ink-700 hover:text-fg" aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <div className="max-h-[70dvh] overflow-y-auto px-5 py-4">{children}</div>
        {footer && <div className="border-t border-ink-700 px-5 py-4">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

export function PageHeader({ title, subtitle, actions, eyebrow }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; eyebrow?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-col gap-3 sm:mb-6 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        {eyebrow && <div className="mb-1 text-xs font-semibold uppercase tracking-[0.14em] text-brand">{eyebrow}</div>}
        <h1 className="text-[22px] font-semibold tracking-tight text-fg sm:text-[26px]">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-fg-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cx("-mx-4 overflow-x-auto sm:-mx-5", className)}>
      <table className="w-full min-w-full border-collapse text-sm">{children}</table>
    </div>
  );
}

export function Th({ children, className, align = "left" }: { children?: ReactNode; className?: string; align?: "left" | "right" | "center" }) {
  return (
    <th className={cx("whitespace-nowrap border-b border-ink-700 px-4 py-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-muted first:pl-4 sm:first:pl-5 last:pr-4 sm:last:pr-5", align === "right" && "text-right", align === "center" && "text-center", className)}>
      {children}
    </th>
  );
}

export function Td({ children, className, align = "left" }: { children?: ReactNode; className?: string; align?: "left" | "right" | "center" }) {
  return (
    <td className={cx("whitespace-nowrap border-b border-ink-750 px-4 py-3 first:pl-4 sm:first:pl-5 last:pr-4 sm:last:pr-5", align === "right" && "num text-right", align === "center" && "text-center", className)}>
      {children}
    </td>
  );
}

export function AssetIcon({ symbol, assetClass, size = 32 }: { symbol: string; assetClass: string; size?: number }) {
  const palette: Record<string, string> = { stock: "#8C9BFF", crypto: "#F2B54A", forex: "#4FC3D9", option: "#C58CFF", index: "#7C8899" };
  const c = palette[assetClass] ?? "#7C8899";
  const label = assetClass === "forex" ? symbol.slice(0, 3) : symbol.replace(/[^A-Z]/g, "").slice(0, assetClass === "crypto" ? 3 : 2);
  return (
    <span
      className="inline-flex shrink-0 items-center justify-center rounded-full font-bold tracking-tight"
      style={{ width: size, height: size, background: `${c}1F`, color: c, fontSize: size * 0.32, boxShadow: `inset 0 0 0 1px ${c}33` }}
      aria-hidden
    >
      {label}
    </span>
  );
}
