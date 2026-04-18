import "dotenv/config";
import { Worker } from "@snipervrt/orchestrator";
import { boot } from "@snipervrt/api";

// The worker uses the same deps as the API so a single-process deployment
// just runs both. For multi-process, boot the dep graph from a shared module
// or point both at the Postgres-backed stores.
const { deps, orchestrator } = boot();
const worker = new Worker(orchestrator, deps.tasks);

const controller = new AbortController();
process.on("SIGINT", () => controller.abort());
process.on("SIGTERM", () => controller.abort());

worker.run({ pollIntervalMs: 500, signal: controller.signal }).catch((e) => {
  console.error("worker crashed", e);
  process.exit(1);
});
