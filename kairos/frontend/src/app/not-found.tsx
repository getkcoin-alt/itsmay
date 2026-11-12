import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-ink-950 p-6 text-center">
      <div>
        <div className="num text-5xl font-semibold text-fg-faint">404</div>
        <h1 className="mt-2 text-lg font-semibold text-fg">Page not found</h1>
        <p className="mt-1 text-sm text-fg-muted">The page you are looking for does not exist in this demo.</p>
        <Link href="/dashboard" className="mt-5 inline-block rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-ink-950">Go to dashboard</Link>
      </div>
    </div>
  );
}
