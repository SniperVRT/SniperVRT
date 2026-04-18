import { defineConfig } from "vitest/config";
import { resolve } from "node:path";

// Vitest reads TS directly. We alias the workspace package names to their
// source entries so tests don't require a prior `tsc -b` build. Production
// consumers still resolve via each package's "main"/"exports" in package.json.
const pkg = (sub: string) => resolve(__dirname, "packages", sub, "src/index.ts");

export default defineConfig({
  resolve: {
    alias: {
      "@snipervrt/shared": pkg("shared"),
      "@snipervrt/connector-core": pkg("connectors/core"),
      "@snipervrt/prompts": pkg("prompts"),
      "@snipervrt/telemetry": pkg("telemetry"),
      "@snipervrt/planner": pkg("planner"),
      "@snipervrt/policies": pkg("policies"),
      "@snipervrt/memory": pkg("memory"),
      "@snipervrt/state": pkg("state"),
      "@snipervrt/orchestrator": pkg("orchestrator"),
      "@snipervrt/agent-integration": pkg("agents/integration"),
      "@snipervrt/agent-execution": pkg("agents/execution"),
      "@snipervrt/agent-validation": pkg("agents/validation"),
      "@snipervrt/agent-recovery": pkg("agents/recovery"),
      "@snipervrt/agent-security": pkg("agents/security"),
      "@snipervrt/agent-browser": pkg("agents/browser"),
      "@snipervrt/connector-gmail": pkg("connectors/gmail"),
      "@snipervrt/connector-gcal": pkg("connectors/gcal"),
      "@snipervrt/connector-notion": pkg("connectors/notion"),
      "@snipervrt/connector-github": pkg("connectors/github"),
      "@snipervrt/connector-supabase": pkg("connectors/supabase"),
      "@snipervrt/connector-mcp": pkg("connectors/mcp"),
      "@snipervrt/connector-browser": pkg("connectors/browser"),
    },
  },
  test: {
    include: ["tests/**/*.test.ts"],
    environment: "node",
    testTimeout: 10_000,
    reporters: ["default"],
    coverage: {
      reporter: ["text", "html"],
      include: ["packages/**/src/**/*.ts"],
      exclude: ["**/*.d.ts", "**/dist/**"],
    },
  },
});
