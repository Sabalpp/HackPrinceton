import type { Metadata } from "next";
import { ParentScreen } from "@/components/portal/parent";

export const metadata: Metadata = { title: "FarCare · My day" };

export default function ParentPage() {
  return <ParentScreen />;
}
