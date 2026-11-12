"use client";

import {
  ArrowLeftRight,
  Bitcoin,
  BriefcaseBusiness,
  CandlestickChart,
  ChevronDown,
  Eye,
  Gauge,
  History,
  Layers,
  LayoutGrid,
  LogOut,
  Menu,
  ReceiptText,
  Search,
  Settings,
  Shield,
  SquareStack,
  User as UserIcon,
  Wallet,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { api, useApi } from "@/lib/api";
import { inr } from "@/lib/format";
import type { Summary } from "@/lib/types";
import { cx, SimBadge } from "../ui";
import { Logo } from "./logo";
import { SearchPalette } from "./search";
import { SessionProvider, useSession } from "./session";

const NAV: { section: string; items: { href: string; label: string; icon: typeof Gauge }[] }[] = [
  {
    section: "Overview",
    items: [
      { href: "/dashboard", label: "Dashboard", icon: Gauge },
      { href: "/markets", label: "Markets", icon: LayoutGrid },
      { href: "/watchlist", label: "Watchlist", icon: Eye },
    ],
  },
  {
    section: "Trade",
    items: [
      { href: "/stocks", label: "Stocks", icon: CandlestickChart },
      { href: "/options", label: "Options", icon: Layers },
      { href: "/crypto", label: "Crypto", icon: Bitcoin },
      { href: "/forex", label: "Forex", icon: ArrowLeftRight },
    ],
  },
  {
    section: "Account",
    items: [
      { href: "/portfolio", label: "Portfolio", icon: BriefcaseBusiness },
      { href: "/positions", label: "Positions", icon: SquareStack },
      { href: "/orders", label: "Orders", icon: ReceiptText },
      { href: "/wallet", label: "Wallet", icon: Wallet },
      { href: "/transactions", label: "Transactions", icon: History },
    ],
  },
];

const BOTTOM = [
  { href: "/dashboard", label: "Home", icon: Gauge },
  { href: "/markets", label: "Markets", icon: LayoutGrid },
  { href: "/options", label: "Options", icon: Layers },
  { href: "/portfolio", label: "Portfolio", icon: BriefcaseBusiness },
];

function isActive(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(href + "/");
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <SessionProvider>
      <Shell>{children}</Shell>
    </SessionProvider>
  );
}

function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [searchOpen, setSearchOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const { user } = useSession();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      const typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !typing)) {
        e.preventDefault();
        setSearchOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => setMoreOpen(false), [pathname]);

  return (
    <div className="min-h-dvh bg-ink-950">
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-[232px] flex-col border-r border-ink-750 bg-ink-900/80 backdrop-blur lg:flex">
        <div className="flex h-16 items-center px-5">
          <Logo href="/dashboard" />
        </div>
        <nav className="flex-1 overflow-y-auto px-3 pb-4">
          {NAV.map((group) => (
            <div key={group.section} className="mt-4 first:mt-1">
              <div className="px-2.5 pb-1.5 text-[10px] font-semibold uppercase tracking-[0.16em] text-fg-faint">{group.section}</div>
              {group.items.map(({ href, label, icon: Icon }) => (
                <Link
                  key={href}
                  href={href}
                  className={cx(
                    "group flex items-center gap-3 rounded-lg px-2.5 py-2 text-[13.5px] font-medium transition-colors",
                    isActive(pathname, href) ? "bg-ink-750 text-fg" : "text-fg-muted hover:bg-ink-800 hover:text-fg",
                  )}
                >
                  <Icon size={17} className={cx(isActive(pathname, href) ? "text-brand" : "text-fg-faint group-hover:text-fg-soft")} />
                  {label}
                </Link>
              ))}
            </div>
          ))}
          {user?.role === "admin" && (
            <div className="mt-4">
              <div className="px-2.5 pb-1.5 text-[10px] font-semibold uppercase tracking-[0.16em] text-fg-faint">Back office</div>
              <Link href="/admin" className="flex items-center gap-3 rounded-lg px-2.5 py-2 text-[13.5px] font-medium text-fg-muted hover:bg-ink-800 hover:text-fg">
                <Shield size={17} className="text-fg-faint" /> Admin console
              </Link>
            </div>
          )}
        </nav>
        <div className="border-t border-ink-750 p-4 text-[11px] leading-relaxed text-fg-faint">
          Demonstration environment. Prices are simulated; no real orders, funds or custody.
        </div>
      </aside>

      <div className="lg:pl-[232px]">
        <TopBar onSearch={() => setSearchOpen(true)} />
        <main className="mx-auto w-full max-w-[1440px] px-4 pb-28 pt-4 sm:px-6 sm:pt-6 lg:pb-12">{children}</main>
      </div>

      {/* Mobile bottom navigation */}
      <nav className="pb-safe fixed inset-x-0 bottom-0 z-40 border-t border-ink-700 bg-ink-900/95 backdrop-blur lg:hidden" aria-label="Primary">
        <div className="grid grid-cols-5">
          {BOTTOM.map(({ href, label, icon: Icon }) => (
            <Link key={href} href={href} className={cx("flex flex-col items-center gap-1 py-2.5 text-[10.5px] font-medium", isActive(pathname, href) ? "text-brand" : "text-fg-muted")}>
              <Icon size={20} />
              {label}
            </Link>
          ))}
          <button onClick={() => setMoreOpen(true)} className={cx("flex flex-col items-center gap-1 py-2.5 text-[10.5px] font-medium", moreOpen ? "text-brand" : "text-fg-muted")}>
            <Menu size={20} />
            More
          </button>
        </div>
      </nav>

      {moreOpen && <MoreSheet onClose={() => setMoreOpen(false)} isAdmin={user?.role === "admin"} />}
      <SearchPalette open={searchOpen} onClose={() => setSearchOpen(false)} />
    </div>
  );
}

function TopBar({ onSearch }: { onSearch: () => void }) {
  const { data } = useApi<Summary>("/api/portfolio/summary", { refreshInterval: 15000 });
  return (
    <header className="sticky top-0 z-20 border-b border-ink-750 bg-ink-950/85 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-[1440px] items-center gap-3 px-4 sm:h-16 sm:px-6">
        <div className="lg:hidden">
          <Logo href="/dashboard" compact />
        </div>
        <button
          onClick={onSearch}
          className="flex h-9 min-w-0 flex-1 items-center gap-2 rounded-lg border border-ink-700 bg-ink-900 px-3 text-left text-sm text-fg-faint transition-colors hover:border-ink-600 sm:max-w-md"
        >
          <Search size={15} />
          <span className="truncate">Search stocks, crypto, forex…</span>
          <kbd className="ml-auto hidden rounded border border-ink-600 px-1.5 text-[10px] text-fg-muted sm:inline">/</kbd>
        </button>
        <div className="ml-auto flex items-center gap-3">
          <SimBadge className="hidden md:inline-flex" />
          <Link href="/wallet" className="hidden flex-col items-end leading-tight sm:flex" title="Available demo cash">
            <span className="text-[10px] uppercase tracking-[0.12em] text-fg-faint">Available</span>
            <span className="num text-sm font-semibold text-fg">{data ? inr(data.available_cash) : "—"}</span>
          </Link>
          <UserMenu />
        </div>
      </div>
      <div className="flex items-center justify-center gap-2 border-t border-warn/15 bg-warn-soft py-1 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-warn md:hidden">
        <span className="h-1.5 w-1.5 rounded-full bg-warn" /> Demo · Simulated market data
      </div>
    </header>
  );
}

function UserMenu() {
  const { user } = useSession();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const router = useRouter();
  useEffect(() => {
    const onDoc = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);
  const initials = (user?.full_name || "··").split(" ").map((p) => p[0]).slice(0, 2).join("");
  const logout = async () => {
    await api("/api/auth/logout", { method: "POST" });
    router.replace("/login");
  };
  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen((o) => !o)} className="flex items-center gap-2 rounded-lg p-1 pr-1.5 hover:bg-ink-800" aria-haspopup="menu" aria-expanded={open}>
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-brand/15 text-xs font-bold text-brand">{initials}</span>
        <ChevronDown size={14} className="hidden text-fg-muted sm:block" />
      </button>
      {open && (
        <div role="menu" className="absolute right-0 mt-2 w-60 animate-rise overflow-hidden rounded-xl border border-ink-600 bg-ink-800 shadow-pop">
          <div className="border-b border-ink-700 px-4 py-3">
            <div className="truncate text-sm font-semibold text-fg">{user?.full_name}</div>
            <div className="truncate text-xs text-fg-muted">{user?.email}</div>
          </div>
          <MenuLink href="/profile" icon={UserIcon} label="Profile & KYC" onClick={() => setOpen(false)} />
          <MenuLink href="/settings" icon={Settings} label="Settings" onClick={() => setOpen(false)} />
          {user?.role === "admin" && <MenuLink href="/admin" icon={Shield} label="Admin console" onClick={() => setOpen(false)} />}
          <button onClick={logout} className="flex w-full items-center gap-3 border-t border-ink-700 px-4 py-2.5 text-sm text-down hover:bg-ink-750">
            <LogOut size={16} /> Log out
          </button>
        </div>
      )}
    </div>
  );
}

function MenuLink({ href, icon: Icon, label, onClick }: { href: string; icon: typeof Gauge; label: string; onClick: () => void }) {
  return (
    <Link href={href} onClick={onClick} className="flex items-center gap-3 px-4 py-2.5 text-sm text-fg-soft hover:bg-ink-750 hover:text-fg">
      <Icon size={16} /> {label}
    </Link>
  );
}

function MoreSheet({ onClose, isAdmin }: { onClose: () => void; isAdmin: boolean }) {
  const pathname = usePathname();
  const extra = [
    { href: "/profile", label: "Profile", icon: UserIcon },
    { href: "/settings", label: "Settings", icon: Settings },
    ...(isAdmin ? [{ href: "/admin", label: "Admin", icon: Shield }] : []),
  ];
  return (
    <div className="fixed inset-0 z-50 lg:hidden">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="pb-safe absolute inset-x-0 bottom-0 animate-rise rounded-t-2xl border-t border-ink-600 bg-ink-850 p-4">
        <div className="mb-3 flex items-center justify-between">
          <span className="text-sm font-semibold text-fg">All sections</span>
          <button onClick={onClose} className="rounded-md p-1 text-fg-muted" aria-label="Close menu">
            <X size={18} />
          </button>
        </div>
        <div className="grid grid-cols-4 gap-2">
          {[...NAV.flatMap((g) => g.items), ...extra].map(({ href, label, icon: Icon }) => (
            <Link key={href} href={href} className={cx("flex flex-col items-center gap-1.5 rounded-xl border px-1 py-3 text-[11px] font-medium", isActive(pathname, href) ? "border-brand/40 bg-brand-soft text-brand" : "border-ink-700 bg-ink-800 text-fg-soft")}>
              <Icon size={19} />
              {label}
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
