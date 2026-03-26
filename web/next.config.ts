import type { NextConfig } from "next";
import { resolve } from "path";

const nextConfig: NextConfig = {
  output: "export",
  turbopack: {
    root: resolve(import.meta.dirname),
  },
};

export default nextConfig;
