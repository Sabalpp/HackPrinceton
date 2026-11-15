import type { ReactNode } from "react";

/** 390×844 device frame centered on the page (shrinks to fit on real phones). */
export function PhoneFrame({ children, label, lang }: { children: ReactNode; label: string; lang?: string }) {
  return (
    <main className="flex min-h-screen flex-1 items-center justify-center bg-bg p-4">
      <div
        lang={lang}
        aria-label={label}
        className="flex h-[844px] max-h-[calc(100vh-2rem)] w-[390px] max-w-full flex-col overflow-hidden rounded-[2.5rem] border-[10px] border-ink bg-surface"
      >
        {children}
      </div>
    </main>
  );
}
