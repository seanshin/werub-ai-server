import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  basePath: "/ollama/admin-console",
  trailingSlash: true,
  images: { unoptimized: true },
};

export default nextConfig;
