import type { MetadataRoute } from "next";

/** "Add to Home Screen" opens FarCare full screen like an installed app. */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "FarCare",
    short_name: "FarCare",
    description: "Private check-ins, personal health patterns, and trusted help for families living apart.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#f6f8f7",
    theme_color: "#f6f8f7",
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
