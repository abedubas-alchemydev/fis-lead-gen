/** @type {import('next').NextConfig} */
const nextConfig = {
  // Standalone output for Docker — bundles everything into a self-contained folder
  output: "standalone",
  allowedDevOrigins: ["127.0.0.1:3000", "localhost:3000"],
  typedRoutes: true,
  // Defense-in-depth security headers. CSP is intentionally limited to
  // frame-ancestors today — adding script-src/style-src here would break
  // Next's inline runtime without nonce wiring (deferred to a follow-up).
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Permissions-Policy",
            value: "clipboard-write=(self), clipboard-read=()",
          },
          {
            key: "Strict-Transport-Security",
            value: "max-age=63072000; includeSubDomains; preload",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
