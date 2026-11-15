import { DashboardProvider } from "@/lib/dashboard";
import { AppShell } from "@/components/app/shell";
import { LinkedFamily, RequireRole } from "@/lib/session";

// The family dashboard: one data store (me + overview + timeline + live stream) for every page.
export default function AppLayout({ children }: LayoutProps<"/">) {
  return (
    <RequireRole roles={["child", "admin"]}><LinkedFamily><DashboardProvider>
      <AppShell>{children}</AppShell>
    </DashboardProvider></LinkedFamily></RequireRole>
  );
}
