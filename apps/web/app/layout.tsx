import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import "./globals.css";

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
  description: "A daily check-in call for Ama, and a shared dashboard for her children abroad.",
};

export const viewport: Viewport = {
  themeColor: "#f2f7f6",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${jakarta.variable} ${devanagari.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col">{children}</body>
    </html>
  );
}
