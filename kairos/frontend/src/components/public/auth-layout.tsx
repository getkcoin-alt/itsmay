import Link from "next/link";
import { Logo } from "../layout/logo";

export function AuthLayout({ title, subtitle, children, footer }: { title: string; subtitle?: string; children: React.ReactNode; footer?: React.ReactNode }) {
  return (
    <div className="relative flex min-h-dvh flex-col bg-ink-950">
      <div className="bg-grid pointer-events-none absolute inset-0 [mask-image:radial-gradient(ellipse_at_top,black_30%,transparent_70%)]" />
      <header className="relative flex items-center justify-between px-5 py-5 sm:px-8">
        <Logo />
        <span className="rounded-md border border-warn/30 bg-warn-soft px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.12em] text-warn">Demo environment</span>
      </header>
      <main className="relative flex flex-1 items-start justify-center px-4 pb-16 pt-6 sm:items-center sm:pt-0">
        <div className="w-full max-w-[420px]">
          <h1 className="text-2xl font-semibold tracking-tight text-fg">{title}</h1>
          {subtitle && <p className="mt-1.5 text-sm text-fg-muted">{subtitle}</p>}
          <div className="mt-6 rounded-2xl border border-ink-700 bg-ink-850/90 p-5 shadow-pop backdrop-blur sm:p-6">{children}</div>
          {footer && <div className="mt-5 text-center text-sm text-fg-muted">{footer}</div>}
        </div>
      </main>
      <footer className="relative px-5 pb-6 text-center text-[11px] text-fg-faint">
        Simulated demonstration. No real trading, payments or custody. <Link href="/" className="underline hover:text-fg-muted">About this demo</Link>
      </footer>
    </div>
  );
}
