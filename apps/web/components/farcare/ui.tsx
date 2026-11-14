import Link from "next/link";
import type { ReactNode } from "react";
import { USE_MOCKS } from "@/lib/api";

/* Shared primitives. Flat: 1px borders, no shadows, no gradients. */

export function Card({
  children,
  className = "",
  as: Tag = "section",
}: {
  children: ReactNode;
  className?: string;
  as?: "section" | "div" | "article";
}) {
  return <Tag className={`min-w-0 rounded-2xl border border-border bg-surface p-6 ${className}`}>{children}</Tag>;
}

export function CardTitle({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
      <h2 className="text-lg font-bold text-ink">{children}</h2>
      {aside && <div className="text-sm text-muted">{aside}</div>}
    </div>
  );
}

const BTN = "inline-flex min-h-11 items-center justify-center gap-2 rounded-[10px] px-4 font-semibold transition-colors disabled:opacity-60";
export const btn = {
  primary: `${BTN} bg-primary text-white hover:bg-primary-hover`,
  outline: `${BTN} border border-border bg-surface text-ink hover:border-primary hover:text-primary`,
  quiet: `${BTN} text-primary hover:bg-primary-tint`,
};

export function ButtonLink({ href, variant = "primary", className = "", children }: {
  href: string;
  variant?: keyof typeof btn;
  className?: string;
  children: ReactNode;
}) {
  return (
    <Link href={href} className={`${btn[variant]} ${className}`}>
      {children}
    </Link>
  );
}

export type PillTone = "good" | "watch" | "urgent" | "info" | "neutral" | "tint";

const PILL: Record<PillTone, string> = {
  good: "bg-good-bg text-good",
  watch: "bg-watch-bg text-watch-label",
  urgent: "bg-danger text-white",
  info: "bg-info-bg text-info",
  neutral: "bg-neutral-bg text-neutral",
  tint: "bg-primary-tint text-on-tint",
};

export function StatusPill({ tone, children }: { tone: PillTone; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center whitespace-nowrap rounded-full px-3 py-1 text-sm font-semibold ${PILL[tone]}`}>
      {children}
    </span>
  );
}

/** Rule 15: synthetic data is always labeled. Shown only in mock mode. */
export function SimulatedPill() {
  if (!USE_MOCKS) return null;
  return (
    <span className="inline-flex items-center whitespace-nowrap rounded-full border border-dashed border-muted/50 px-2.5 py-0.5 text-xs font-semibold text-muted">
      Simulated
    </span>
  );
}

/** Nepali text: correct font, and `lang` so screen readers switch voice. */
export function Ne({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <span lang="ne" className={`font-ne ${className}`}>
      {children}
    </span>
  );
}
