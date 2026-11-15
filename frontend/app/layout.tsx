import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import "./globals.css";
import { SessionProvider } from "@/lib/session";

const jakarta = localFont({
  src: "./fonts/plus-jakarta-sans-latin.woff2",
  variable: "--font-jakarta",
  weight: "400 700",
  display: "swap",
});

const devanagari = localFont({
  src: "./fonts/noto-sans-devanagari.woff2",
  variable: "--font-devanagari",
  weight: "400 700",
  display: "swap",
});

export const metadata: Metadata = {
  title: "FarCare",
  description: "Private check-ins, personal health patterns, and trusted help for families living apart.",
};

export const viewport: Viewport = {
  themeColor: "#f6f8f7",
  viewportFit: "cover", // lets the phone dock pad itself for the home indicator (env(safe-area-inset-*))
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${jakarta.variable} ${devanagari.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col"><SessionProvider>{children}</SessionProvider></body>
    </html>
  );
}
