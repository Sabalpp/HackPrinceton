import type { Metadata } from "next";
import { SetupScreen } from "@/components/portal/setup";

export const metadata: Metadata = { title: "FarCare · Setup" };

export default function SetupPage() {
  return <SetupScreen />;
}
