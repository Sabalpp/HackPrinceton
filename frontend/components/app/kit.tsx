import Link from "next/link";
import type { ReactNode } from "react";
import {
  ArrowRight,
  Car,
  HandHeart,
  MessageCircleHeart,
  Pill,
  RefreshCw,
  ShoppingBasket,
  Siren,
  Utensils,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import type { AssistanceCategory } from "@/lib/api";
import type { Tone } from "@/lib/format";
import type { PillTone } from "@/components/farcare/ui";

export const CATEGORY_ICON: Record<AssistanceCategory, LucideIcon> = {
  grocery_assistance: ShoppingBasket,
  transportation: Car,
  meal_assistance: Utensils,
  medication_pickup: Pill,
  household_help: Wrench,
  companionship: MessageCircleHeart,
  urgent_check_in: Siren,
  other: HandHeart,
};

/** Status meaning (lib/format Tone) -> pill colour. */
export const toneToPill: Record<Tone, PillTone> = {
  waiting: "watch",
  coming: "tint",
  urgent: "urgent",
  done: "good",
  neutral: "neutral",
};

/** Standard page header: optional context line, one title, one sentence, actions on the right. */
export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: ReactNode;
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div className="flex min-w-0 flex-col gap-1">
        {eyebrow && <p className="text-sm font-medium text-muted">{eyebrow}</p>}
        <h1 className="text-3xl font-bold tracking-tight text-ink sm:text-4xl">{title}</h1>
        {description && <div className="max-w-2xl text-body">{description}</div>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </header>
  );
}

/** Standard card: a header row (title, optional note, action) above the content. */
export function Card({
  title,
  icon: Icon,
  description,
  action,
  children,
  className = "",
  bodyClassName = "p-5",
  testId,
}: {
  title?: ReactNode;
  icon?: LucideIcon;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  testId?: string;
}) {
  return (
    <section data-testid={testId} className={`min-w-0 overflow-hidden rounded-2xl border border-border bg-surface shadow-card ${className}`}>
      {(title || action) && (
        <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-border px-5 py-3.5">
          <div className="min-w-0">
            <h2 className="flex items-center gap-2 text-base font-semibold text-ink">
              {Icon && <Icon size={18} className="shrink-0 text-primary" aria-hidden="true" />}
              {title}
            </h2>
            {description && <p className="text-sm text-muted">{description}</p>}
          </div>
          {action && <div className="flex flex-wrap items-center gap-2">{action}</div>}
        </header>
      )}
      <div className={bodyClassName}>{children}</div>
    </section>
  );
}

/** "See all" style link used in card headers. */
export function CardLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link href={href} className="inline-flex min-h-11 items-center gap-1 text-sm font-semibold text-primary hover:underline">
      {children} <ArrowRight size={15} aria-hidden="true" />
    </Link>
  );
}

export function LoadingBlock({ label = "Loading", rows = 3 }: { label?: string; rows?: number }) {
  return (
    <div aria-busy="true" aria-label={label} className="flex flex-col gap-3">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="h-24 animate-pulse rounded-2xl bg-border/60" />
      ))}
    </div>
  );
}

export function EmptyBlock({ title, children, icon: Icon }: { title: string; children?: ReactNode; icon?: LucideIcon }) {
  return (
    <div className="flex items-start gap-3 rounded-2xl border border-dashed border-border bg-inset p-5">
      {Icon && <Icon size={22} className="mt-0.5 shrink-0 text-muted" aria-hidden="true" />}
      <div className="min-w-0 [overflow-wrap:anywhere]">
        <p className="font-semibold text-ink">{title}</p>
        {children && <div className="mt-0.5 text-body">{children}</div>}
      </div>
    </div>
  );
}

export function ErrorBlock({ title, message, onRetry }: { title: string; message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded-2xl border border-border bg-surface p-6">
      <p className="text-lg font-semibold text-ink">{title}</p>
      <p className="mt-1 text-body">{message}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-4 inline-flex min-h-11 items-center gap-2 rounded-[10px] border border-border bg-surface px-4 font-semibold text-ink hover:border-primary hover:text-primary"
        >
          <RefreshCw size={18} aria-hidden="true" />
          Try again
        </button>
      )}
    </div>
  );
}

export function Avatar({ name, size = "md", tone = "tint" }: { name: string; size?: "sm" | "md" | "lg"; tone?: "tint" | "sun" }) {
  const s = size === "lg" ? "size-14 text-xl" : size === "sm" ? "size-8 text-sm" : "size-11 text-base";
  const t = tone === "sun" ? "bg-coral-tint text-coral-ink" : "bg-primary-tint text-on-tint";
  return (
    <span className={`grid shrink-0 place-items-center rounded-full font-bold ${s} ${t}`} aria-hidden="true">
      {name.charAt(0).toUpperCase()}
    </span>
  );
}

export function SectionTitle({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 className="text-lg font-semibold text-ink">{children}</h2>
      {action}
    </div>
  );
}
