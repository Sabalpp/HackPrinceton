import type { Metadata } from "next";
import { RequestsScreen } from "@/components/app/screens/requests";

export const metadata: Metadata = { title: "FarCare · Help requests" };

export default function RequestsPage() {
  return <RequestsScreen />;
}
