import type { Metadata } from "next";
import { CircleScreen } from "@/components/app/screens/circle";

export const metadata: Metadata = { title: "FarCare · Care Circle" };

export default function CirclePage() {
  return <CircleScreen />;
}
