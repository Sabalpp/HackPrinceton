import { Shell, Sidebar } from "@/components/farcare/sidebar";

// Shared sidebar + main column for Today, Thread, Live call and Emergency.
export default function WebLayout({ children }: LayoutProps<"/">) {
  return <Shell sidebar={<Sidebar />}><aside className="mb-6 rounded-xl border border-watch-border bg-watch-bg p-4 text-sm text-watch-body"><strong>Design concept.</strong> These screens illustrate future features with sample data. They are separate from your signed-in FarCare account.</aside>{children}</Shell>;
}
