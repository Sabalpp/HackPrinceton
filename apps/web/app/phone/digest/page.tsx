import type { Metadata } from "next";
import { PhoneDigest } from "@/components/farcare/phone-digest";

export const metadata: Metadata = { title: "FarCare · Morning update" };

export default function PhoneDigestPage() {
  return <PhoneDigest />;
}
