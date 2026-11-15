import type { Metadata } from "next";
import { PhoneDigest } from "@/components/farcare/phone-digest";

export const metadata: Metadata = { title: "FarCare · Morning update" };

export default function PhoneDigestPage() {
  return <><aside className="border-b border-watch-border bg-watch-bg p-4 text-center text-sm text-watch-body"><strong>Design concept.</strong> Sample messages only. This screen does not send messages or schedule calls.</aside><PhoneDigest /></>;
}
