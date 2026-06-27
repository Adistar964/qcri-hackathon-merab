/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Proxy API calls to the FastAPI backend during development so the frontend
  // can call /api/* without CORS or hard-coded hosts.
  async rewrites() {
    const backend = process.env.BACKEND_URL || "http://localhost:8008";
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};
module.exports = nextConfig;
