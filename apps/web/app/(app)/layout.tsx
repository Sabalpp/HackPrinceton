import { DashboardProvider } from "@/lib/dashboard";
import { AppShell } from "@/components/app/shell";

// The family dashboard: one data store (me + overview + timeline + live stream) for every page.
export default function AppLayout({ children }: LayoutProps<"/">) {
  return (
    <DashboardProvider>
      <AppShell>{children}</AppShell>
    </DashboardProvider>
  );
}
