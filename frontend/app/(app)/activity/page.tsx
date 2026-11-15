import type { Metadata } from "next";
import { ActivityScreen } from "@/components/app/screens/activity";

export const metadata: Metadata = { title: "FarCare · Activity" };

export default function ActivityPage() {
  return <ActivityScreen />;
}
