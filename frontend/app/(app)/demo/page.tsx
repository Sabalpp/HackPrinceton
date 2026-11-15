import type { Metadata } from "next";
import { DemoScreen } from "@/components/app/screens/demo";

export const metadata: Metadata = { title: "FarCare · Demo controls" };

export default function DemoPage() {
  return <DemoScreen />;
}
