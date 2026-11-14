import { Shell, Sidebar } from "@/components/farcare/sidebar";

// Shared sidebar + main column for Today, Thread, Live call and Emergency.
export default function WebLayout({ children }: LayoutProps<"/">) {
  return <Shell sidebar={<Sidebar />}>{children}</Shell>;
}
