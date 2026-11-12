import Link from "next/link";

export function LogoMark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden>
      <rect x="0.5" y="0.5" width="31" height="31" rx="9" fill="#0E121A" stroke="#273043" />
      <path d="M9 23V9" stroke="#8C9BFF" strokeWidth="2.6" strokeLinecap="round" />
      <path d="M23 9.5 13.2 16 23 22.5" stroke="#8C9BFF" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="23.2" cy="16" r="1.9" fill="#22C993" />
    </svg>
  );
}

export function Logo({ href = "/", compact = false }: { href?: string; compact?: boolean }) {
  return (
    <Link href={href} className="flex items-center gap-2.5" aria-label="Kairos Markets home">
      <LogoMark />
      {!compact && (
        <span className="text-[15px] font-semibold tracking-tight text-fg">
          Kairos<span className="text-fg-muted"> Markets</span>
        </span>
      )}
    </Link>
  );
}
