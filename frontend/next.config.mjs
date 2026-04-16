/** @type {import('next').NextConfig} */
const nextConfig = {
  // Standalone output for Docker — bundles everything into a self-contained folder
  output: "standalone",
  allowedDevOrigins: ["127.0.0.1:3000", "localhost:3000"],
  experimental: {
    typedRoutes: true,
  },
};

export default nextConfig;
