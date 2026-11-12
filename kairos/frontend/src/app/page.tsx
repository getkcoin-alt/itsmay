import { ArrowLeftRight, ArrowRight, Bitcoin, BriefcaseBusiness, CandlestickChart, Layers, Lock, Server, ShieldCheck, Smartphone, Workflow } from "lucide-react";
import Link from "next/link";
import { Logo } from "@/components/layout/logo";

const CLASSES = [
  { icon: CandlestickChart, title: "Stocks", body: "Discovery, watchlists, interactive charts, market & limit orders, holdings with average cost and P&L.", c: "#8C9BFF" },
  { icon: Layers, title: "Options", body: "Full CE/PE chain with OI, volume, IV and Greeks. Buy or write options with SPAN-style margin and explicit risk disclosures.", c: "#C58CFF" },
  { icon: Bitcoin, title: "Crypto", body: "BTC, ETH, BNB, SOL, XRP and more on a simulated INR spot market with fractional quantities.", c: "#F2B54A" },
  { icon: ArrowLeftRight, title: "Forex", body: "USD/INR, EUR/USD, GBP/USD, USD/JPY and more as leveraged margin positions — configurable per jurisdiction.", c: "#4FC3D9" },
  { icon: BriefcaseBusiness, title: "Portfolio", body: "One consolidated view: account value, invested amount, realised & unrealised P&L, allocation and performance history.", c: "#22C993" },
];

const PLATFORM = [
  { icon: Workflow, title: "Real order engine", body: "Server-side validation, fills, weighted-average cost, realised P&L, fees, reservations for resting limit orders and automatic expiry settlement — all inside one locked transaction per account." },
  { icon: ShieldCheck, title: "Risk built in", body: "Margin checks before execution, insufficient-funds and holdings rejections, unlimited-loss acknowledgement for uncovered calls, live margin utilisation." },
  { icon: Server, title: "Back office", body: "Role-based admin console for users, simulated KYC review, instrument controls, price management, fee configuration, analytics and audit logs." },
  { icon: Smartphone, title: "Mobile-first", body: "Responsive layouts, bottom navigation and touch-friendly order entry. A clean REST API with bearer-token support so native apps reuse the same business logic." },
];

export default function Landing() {
  return (
    <div className="min-h-dvh bg-ink-950">
      <div className="border-b border-warn/20 bg-warn-soft px-4 py-2 text-center text-[11.5px] font-medium text-warn">
        Product demonstration · all market data is simulated · no real trading, payments or custody
      </div>
      <header className="mx-auto flex max-w-6xl items-center justify-between px-5 py-5">
        <Logo />
        <nav className="flex items-center gap-2">
          <Link href="/login" className="rounded-lg px-3 py-2 text-sm font-medium text-fg-soft hover:text-fg">Sign in</Link>
          <Link href="/register" className="rounded-lg bg-brand px-3.5 py-2 text-sm font-semibold text-ink-950 hover:bg-[#a3afff]">Get started</Link>
        </nav>
      </header>

      <section className="relative overflow-hidden">
        <div className="bg-grid pointer-events-none absolute inset-0 [mask-image:radial-gradient(ellipse_at_top,black_20%,transparent_70%)]" />
        <div className="relative mx-auto max-w-6xl px-5 pb-16 pt-12 sm:pt-20">
          <div className="max-w-3xl">
            <span className="inline-flex items-center gap-2 rounded-full border border-ink-600 bg-ink-850 px-3 py-1 text-xs text-fg-soft">
              <span className="h-1.5 w-1.5 rounded-full bg-up" /> Interactive multi-asset platform demo
            </span>
            <h1 className="mt-5 text-[40px] font-semibold leading-[1.05] tracking-tight text-fg sm:text-6xl">
              One account.<br />
              <span className="text-fg-muted">Every market that matters.</span>
            </h1>
            <p className="mt-5 max-w-2xl text-[17px] leading-relaxed text-fg-soft">
              Kairos brings stocks, options, crypto and forex into a single trading and investment experience — with a real execution engine, portfolio accounting and a complete back office. Explore it end to end with a pre-funded demo account.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link href="/login" className="inline-flex h-12 items-center gap-2 rounded-xl bg-brand px-5 text-[15px] font-semibold text-ink-950 hover:bg-[#a3afff]">
                Launch the demo <ArrowRight size={17} />
              </Link>
              <Link href="/register" className="inline-flex h-12 items-center rounded-xl border border-ink-600 bg-ink-850 px-5 text-[15px] font-medium text-fg hover:border-ink-500">
                Create your own demo account
              </Link>
            </div>
          </div>

          <div className="mt-14 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            {CLASSES.map(({ icon: Icon, title, body, c }) => (
              <div key={title} className="rounded-2xl border border-ink-700 bg-ink-850/80 p-5">
                <span className="inline-flex h-9 w-9 items-center justify-center rounded-lg" style={{ background: `${c}1A`, color: c }}>
                  <Icon size={18} />
                </span>
                <h3 className="mt-4 text-[15px] font-semibold text-fg">{title}</h3>
                <p className="mt-1.5 text-[13px] leading-relaxed text-fg-muted">{body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="border-t border-ink-750 bg-ink-900/40">
        <div className="mx-auto max-w-6xl px-5 py-16">
          <h2 className="text-2xl font-semibold tracking-tight text-fg sm:text-3xl">Built like a production platform</h2>
          <p className="mt-2 max-w-2xl text-fg-muted">Not a static mock-up. Every button changes real state on the server, and every number reconciles to a ledger.</p>
          <div className="mt-10 grid gap-5 md:grid-cols-2">
            {PLATFORM.map(({ icon: Icon, title, body }) => (
              <div key={title} className="flex gap-4 rounded-2xl border border-ink-700 bg-ink-850 p-5">
                <Icon size={20} className="mt-0.5 shrink-0 text-brand" />
                <div>
                  <h3 className="font-semibold text-fg">{title}</h3>
                  <p className="mt-1 text-sm leading-relaxed text-fg-muted">{body}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-5 py-16">
        <div className="grid gap-5 md:grid-cols-2">
          <div className="rounded-2xl border border-ink-700 bg-ink-850 p-6">
            <h3 className="flex items-center gap-2 font-semibold text-fg"><Lock size={17} className="text-up" /> What this demo is</h3>
            <ul className="mt-3 space-y-2 text-sm text-fg-soft">
              <li>• A working web application with authentication, RBAC and audit logging</li>
              <li>• A deterministic market simulator, so every demo run is reproducible</li>
              <li>• A simulated order, margin, portfolio and wallet engine</li>
              <li>• A foundation that can be connected to licensed data and execution partners</li>
            </ul>
          </div>
          <div className="rounded-2xl border border-ink-700 bg-ink-850 p-6">
            <h3 className="font-semibold text-fg">What it is not</h3>
            <ul className="mt-3 space-y-2 text-sm text-fg-soft">
              <li>• Not connected to any exchange, broker, bank, payment or custody provider</li>
              <li>• Not real-time or real market prices — every quote is simulated</li>
              <li>• Not investment advice, and makes no claim of regulatory approval</li>
              <li>• Binary options are not offered</li>
            </ul>
          </div>
        </div>
      </section>

      <footer className="border-t border-ink-750">
        <div className="mx-auto flex max-w-6xl flex-col items-start justify-between gap-3 px-5 py-8 text-xs text-fg-faint sm:flex-row sm:items-center">
          <Logo />
          <span>© {new Date().getFullYear()} Kairos Markets demonstration build. Simulated data only.</span>
        </div>
      </footer>
    </div>
  );
}
