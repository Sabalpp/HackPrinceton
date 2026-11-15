"use client";

import Link from "next/link";
import { ArrowLeft, HeartHandshake, LogOut, ShieldCheck } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";

export const inputClass = "min-h-13 min-w-0 w-full max-w-full rounded-xl border border-border bg-surface px-4 py-3 text-lg text-ink placeholder:text-muted focus:border-primary";

export function PortalButton({ variant = "primary", className = "", children, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "quiet";
}) {
  const tone = variant === "primary" ? "border-primary bg-primary text-on-primary hover:bg-primary-hover"
    : variant === "secondary" ? "border-border bg-surface text-ink hover:border-primary hover:bg-inset"
      : "border-transparent text-primary hover:bg-primary-tint";
  return <button type="button" {...props} className={`inline-flex min-h-13 min-w-0 max-w-full touch-manipulation items-center justify-center gap-2 whitespace-normal rounded-xl border px-5 py-3 text-base font-bold [overflow-wrap:anywhere] transition-colors disabled:cursor-wait disabled:opacity-60 [&>svg]:shrink-0 ${tone} ${className}`}>{children}</button>;
}

export function PortalShell({ children, name, roleLabel, onLogout, preview = false, wide = false }: {
  children: ReactNode;
  name: string;
  roleLabel: string;
  onLogout: () => void;
  preview?: boolean;
  wide?: boolean;
}) {
  return (
    <div className="min-h-dvh bg-bg">
      <a href="#portal-main" className="sr-only rounded-lg bg-primary p-3 text-on-primary focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50">Skip to content</a>
      <header className="border-b border-border bg-surface pt-[env(safe-area-inset-top)]">
        <div className={`mx-auto flex flex-wrap items-center justify-between gap-3 px-5 py-4 ${wide ? "max-w-6xl" : "max-w-3xl"}`}>
          <div className="flex shrink-0 items-center gap-2 text-xl font-bold tracking-tight text-primary"><HeartHandshake size={28} aria-hidden="true" /> FarCare</div>
          <div className="ml-auto flex min-w-0 max-w-full items-center gap-2 sm:gap-4">
            <div className="min-w-0 text-right [overflow-wrap:anywhere]"><p className="font-semibold text-ink">{name}</p><p className="text-sm text-muted">{roleLabel}</p></div>
            <button type="button" onClick={onLogout} className="flex min-h-11 shrink-0 items-center gap-1.5 rounded-lg px-2 text-sm font-semibold text-muted hover:bg-inset hover:text-ink"><LogOut size={17} aria-hidden="true" /> Sign out</button>
          </div>
        </div>
      </header>
      <main id="portal-main" tabIndex={-1} className={`mx-auto flex min-w-0 w-full flex-col gap-6 px-5 pb-[calc(2rem+env(safe-area-inset-bottom))] pt-8 [overflow-wrap:anywhere] sm:pt-10 ${wide ? "max-w-6xl" : "max-w-3xl"}`}>
        {preview && <p role="status" className="rounded-xl border border-watch-border bg-watch-bg px-4 py-3 text-sm text-watch-body"><strong>Demo account.</strong> This session uses a sample person. Health records retain their own source and dates.</p>}
        {children}
        <footer className="mt-6 flex items-start gap-2 border-t border-border pt-5 text-sm text-muted"><ShieldCheck size={19} className="shrink-0" aria-hidden="true" /><p>You choose who can help. FarCare coordinates care; it is not a medical service and does not contact emergency services.</p></footer>
      </main>
    </div>
  );
}

export function PortalCard({ title, description, children, className = "" }: { title?: string; description?: string; children: ReactNode; className?: string }) {
  return <section className={`min-w-0 rounded-3xl border border-border bg-surface p-5 sm:p-7 ${className}`}>{title && <h2 className="text-xl font-bold text-ink">{title}</h2>}{description && <p className="mt-1 text-body">{description}</p>}<div className={title || description ? "mt-5" : ""}>{children}</div></section>;
}

export function PortalMessage({ children, error = false }: { children: ReactNode; error?: boolean }) {
  return <p role={error ? "alert" : "status"} className={`rounded-xl border px-4 py-3 text-base [overflow-wrap:anywhere] ${error ? "border-danger/25 bg-danger-light text-danger" : "border-good-border bg-good-bg text-good-strong"}`}>{children}</p>;
}

export function PortalBack({ onClick, children = "Back" }: { onClick: () => void; children?: ReactNode }) {
  return <button type="button" onClick={onClick} className="inline-flex min-h-12 items-center gap-2 self-start rounded-lg px-2 text-lg font-semibold text-primary hover:bg-primary-tint"><ArrowLeft size={21} aria-hidden="true" />{children}</button>;
}

export function AuthFrame({ children, title, description }: { children: ReactNode; title: string; description: string }) {
  return <main className="grid min-h-dvh grid-rows-[auto_1fr] bg-bg lg:grid-cols-[1fr_1fr] lg:grid-rows-1">
    <aside className="relative flex flex-col justify-between overflow-hidden bg-hero px-5 pb-5 pt-[calc(1.25rem+env(safe-area-inset-top))] text-white sm:px-12 lg:px-16 lg:py-14">
      <div className="flex items-center gap-3 text-2xl font-bold"><HeartHandshake size={34} aria-hidden="true" />FarCare</div>
      <p className="mt-2 text-sm font-semibold text-on-tint lg:hidden">Far away. Always there.</p>
      <div className="relative my-24 hidden max-w-lg lg:block"><p className="mb-4 text-sm font-bold uppercase tracking-[.16em] text-on-tint">Far away. Always there.</p><h2 className="text-5xl font-bold leading-tight tracking-tight">A little closer,<br />every day.</h2><p className="mt-6 max-w-sm text-lg leading-relaxed text-on-tint">Small check-ins. Trusted people nearby. A family that stays connected.</p></div>
      <p className="hidden max-w-md items-start gap-2 text-sm text-on-tint lg:flex"><ShieldCheck size={20} className="shrink-0" aria-hidden="true" />Your family&apos;s information is shared only with the people you authorize.</p>
      <div aria-hidden="true" className="pointer-events-none absolute -bottom-28 -right-28 size-80 rounded-full border-[40px] border-white/5" />
    </aside>
    <section className="flex min-w-0 items-start justify-center px-5 pb-[calc(2rem+env(safe-area-inset-bottom))] pt-7 sm:p-12 lg:items-center"><div className="min-w-0 w-full max-w-md [overflow-wrap:anywhere]"><h1 className="text-3xl font-bold tracking-tight text-ink">{title}</h1><p className="mb-6 mt-2 text-body">{description}</p>{children}<p className="mt-7 flex items-start gap-2 text-sm text-muted lg:hidden"><ShieldCheck size={20} className="shrink-0" aria-hidden="true" />Your family&apos;s information is shared only with the people you authorize.</p><p className="mt-6 text-sm text-muted">Need an invitation? Ask the person helping set up your family&apos;s FarCare account.</p><Link href="/login" className="mt-2 inline-flex min-h-11 items-center text-sm font-semibold text-primary hover:underline">Return to sign in</Link></div></section>
  </main>;
}
