import type { Metadata } from "next";
import { SafetyScreen } from "@/components/app/screens/safety";

export const metadata: Metadata = { title: "FarCare · Safety alerts" };

export default function SafetyPage() {
  return <SafetyScreen />;
}
