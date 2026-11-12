"use client";

import { ArrowLeft, BarChart3, Boxes, FileCheck2, Gauge, LogOut, ReceiptText, Settings2, ShieldAlert, Users, Wallet } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { SessionProvider, useSession } from "../layout/session";
import { Logo } from "../layout/logo";
import { cx, Skeleton } from "../ui";
import { api } from "@/lib/api";

const NAV = [
  { href: "/admin", label: "Overview", icon: Gauge },
  { href: "/admin/users", label: "Users", icon: Users },
  { href: "/admin/kyc", label: "KYC review", icon: FileCheck2 },
  { href: "/admin/assets", label: "Instruments", icon: Boxes },
  { href: "/admin/orders", label: "Orders", icon: ReceiptText },
  { href: "/admin/transactions", label: "Transactions", icon: Wallet },
  { href: "/admin/reports", label: "Reports & audit", icon: BarChart3 },
  { href: "/admin/settings", label: "Platform settings", icon: Settings2 },
];

export function AdminShell({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <Guard>{children}</Guard>
    </SessionProvider>
  );
}

function Guard({ children }: { children: React.ReactNode }) {
  const { user, loading } = useSession();
  const pathname = usePathname();
  const router = useRouter();
  if (loading || !user) return <div className="p-6"><Skeleton className="h-96" /></div>;
  if (user.role !== "admin")
    return (
      <div className="flex min-h-dvh items-center justify-center bg-ink-950 p-6 text-center">
        <div>
          <ShieldAlert size={32} className="mx-auto text-down" />
          <h1 className="mt-3 text-lg font-semibold text-fg">Administrator access required</h1>
          <p className="mt-1 text-sm text-fg-muted">Your account does not have permission to view the back office.</p>
          <Link href="/dashboard" className="mt-5 inline-block rounded-lg bg-ink-700 px-4 py-2 text-sm text-fg">Back to dashboard</Link>
        </div>
      </div>
    );
  const active = (h: string) => (h === "/admin" ? pathname === "/admin" : pathname.startsWith(h));
  return (
    <div className="min-h-dvh bg-ink-950">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-[232px] flex-col border-r border-ink-750 bg-ink-900 lg:flex">
        <div className="flex h-16 items-center gap-2 px-5">
          <Logo href="/admin" />
        </div>
        <div className="mx-4 mb-2 rounded-md border border-down/30 bg-down-soft px-2 py-1 text-center text-[10px] font-bold uppercase tracking-[0.14em] text-down">Back office · Admin</div>
        <nav className="flex-1 space-y-0.5 px-3 py-2">
          {NAV.map(({ href, label, icon: Icon }) => (
            <Link key={href} href={href} className={cx("flex items-center gap-3 rounded-lg px-2.5 py-2 text-[13.5px] font-medium", active(href) ? "bg-ink-750 text-fg" : "text-fg-muted hover:bg-ink-800 hover:text-fg")}>
              <Icon size={17} className={active(href) ? "text-brand" : "text-fg-faint"} /> {label}
            </Link>
          ))}
        </nav>
        <div className="space-y-1 border-t border-ink-750 p-3">
          <Link href="/dashboard" className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-sm text-fg-muted hover:bg-ink-800 hover:text-fg"><ArrowLeft size={15} /> Trading app</Link>
          <button onClick={async () => { await api("/api/auth/logout", { method: "POST" }); router.replace("/login"); }} className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-sm text-down hover:bg-ink-800"><LogOut size={15} /> Log out</button>
        </div>
      </aside>
      <div className="lg:pl-[232px]">
        <header className="sticky top-0 z-20 border-b border-ink-750 bg-ink-950/90 backdrop-blur lg:hidden">
          <div className="flex h-14 items-center justify-between px-4">
            <Logo href="/admin" compact />
            <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-down">Admin</span>
            <Link href="/dashboard" className="text-xs text-fg-muted">App →</Link>
          </div>
          <nav className="flex gap-1 overflow-x-auto px-3 pb-2">
            {NAV.map(({ href, label }) => (
              <Link key={href} href={href} className={cx("whitespace-nowrap rounded-md px-2.5 py-1.5 text-xs font-medium", active(href) ? "bg-ink-700 text-fg" : "text-fg-muted")}>{label}</Link>
            ))}
          </nav>
        </header>
        <main className="mx-auto max-w-[1400px] px-4 py-5 sm:px-6 sm:py-6">{children}</main>
      </div>
    </div>
  );
}
