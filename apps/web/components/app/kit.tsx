import type { ReactNode } from "react";
import {
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

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div className="flex min-w-0 flex-col gap-1">
        {eyebrow && <p className="text-sm font-semibold text-primary">{eyebrow}</p>}
        <h1 className="text-3xl font-bold tracking-tight text-ink sm:text-4xl">{title}</h1>
        {description && <p className="max-w-2xl text-body">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </header>
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
      <div>
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
      <h2 className="text-lg font-bold text-ink">{children}</h2>
      {action}
    </div>
  );
}
