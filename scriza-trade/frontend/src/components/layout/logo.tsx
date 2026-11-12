import Link from "next/link";

export function LogoMark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden>
      <rect x="0.5" y="0.5" width="31" height="31" rx="9" fill="#0E121A" stroke="#273043" />
      <path d="M22.5 10.2c-1.1-1.3-2.9-2-4.9-2-3.2 0-5.4 1.7-5.4 4.1 0 2.3 1.9 3.1 4.8 3.7 2.9.6 4.9 1.4 4.9 3.8 0 2.5-2.3 4.1-5.6 4.1-2.3 0-4.3-.8-5.6-2.3" stroke="#8C9BFF" strokeWidth="2.6" strokeLinecap="round" />
      <circle cx="22.6" cy="10.1" r="1.9" fill="#22C993" />
    </svg>
  );
}

export function Logo({ href = "/", compact = false }: { href?: string; compact?: boolean }) {
  return (
    <Link href={href} className="flex items-center gap-2.5" aria-label="Scriza Trade home">
      <LogoMark />
      {!compact && (
        <span className="text-[15px] font-semibold tracking-tight text-fg">
          Scriza<span className="text-fg-muted"> Trade</span>
        </span>
      )}
    </Link>
  );
}
