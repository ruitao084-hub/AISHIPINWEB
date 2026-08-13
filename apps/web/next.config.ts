import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Traces which files the server actually needs and emits a self-contained
  // bundle, so the production image carries neither the build toolchain nor a
  // full `node_modules` (§100, P23-T01). For this app that is ~180 MB rather
  // than ~1.2 GB, and every megabyte is pulled onto every node on every
  // deploy.
  output: "standalone",

  // The workspace root, not `apps/web`. Without it the tracer walks up from
  // the app directory, finds several lockfiles in a pnpm monorepo and picks
  // one — usually the wrong one, producing a bundle missing its dependencies.
  outputFileTracingRoot: new URL("../../", import.meta.url).pathname,
};

export default nextConfig;
