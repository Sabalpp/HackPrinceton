import type { Metadata } from "next";
import { SetupWizard } from "@/components/farcare/setup-wizard";

export const metadata: Metadata = { title: "FarCare · Setup" };

export default function SetupPage() {
  return <SetupWizard />;
}
