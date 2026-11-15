import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  cacheComponents: true,
  partialPrefetching: true,
  rewrites() {
    // Native Node hosts can proxy the existing API without a separate gateway.
    // Keeping requests on the dashboard origin preserves session cookies and CSRF.
    const origin = process.env.FARCARE_API_ORIGIN?.replace(/\/+$/, "");
    return origin ? [{ source: "/backend/:path*", destination: `${origin}/:path*` }] : [];
  },
  headers() {
    return [{
      source: "/:path*",
      headers: [
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "X-Frame-Options", value: "DENY" },
        { key: "Referrer-Policy", value: "no-referrer" },
        { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
      ],
    }];
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
