import type { Metadata } from "next";
import { HealthScreen } from "@/components/app/screens/health";

export const metadata: Metadata = { title: "FarCare · Health" };

export default function HealthPage() {
  return <HealthScreen />;
}
