import type { Metadata } from "next";
import { ParentMode } from "@/components/farcare/parent-mode";

export const metadata: Metadata = { title: "FarCare · आमा" };

export default function ParentPage() {
  return <ParentMode />;
}
