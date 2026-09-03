import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Do not emit tool-specific rule files into the repository.
  agentRules: false,
};

export default nextConfig;
