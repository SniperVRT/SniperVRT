import "dotenv/config";
import express from "express";
import { loggerFor, InMemoryTraceRecorder, metrics } from "@snipervrt/telemetry";
import { ConnectorRegistry } from "@snipervrt/connector-core";
import {
  Orchestrator,
  intakeTask,
  type OrchestratorDeps,
} from "@snipervrt/orchestrator";
import { InMemoryTaskStore, InMemoryStepStore } from "@snipervrt/state";
import {
  PolicyEngine,
  DEFAULT_POLICY_BUNDLE,
  InMemoryApprovalStore,
} from "@snipervrt/policies";
import { InMemoryMemoryStore } from "@snipervrt/memory";
import { AnthropicClaudeClient, Planner, Budget } from "@snipervrt/planner";
import { IntegrationAgent } from "@snipervrt/agent-integration";
import { ExecutionAgent } from "@snipervrt/agent-execution";
import { ValidationAgent } from "@snipervrt/agent-validation";
import { RecoveryAgent } from "@snipervrt/agent-recovery";
import { SecurityAgent } from "@snipervrt/agent-security";

// ----------------------------------------------------------------------------
// Boot. Wires the system using in-memory backends by default. Swap TaskStore,
// StepStore, MemoryStore, TraceRecorder, ApprovalStore for their Postgres /
// Redis counterparts to go multi-node.
// ----------------------------------------------------------------------------

const log = loggerFor({ component: "api" });

function boot(): { app: express.Express; orchestrator: Orchestrator; deps: OrchestratorDeps } {
  const registry = new ConnectorRegistry();
  const tasks = new InMemoryTaskStore();
  const steps = new InMemoryStepStore();
  const approvals = new InMemoryApprovalStore();
  const memory = new InMemoryMemoryStore();
  const traces = new InMemoryTraceRecorder();
  const policy = new PolicyEngine(DEFAULT_POLICY_BUNDLE);

  const budget = new Budget({
    maxTokens: Number(process.env.PER_TASK_TOKEN_BUDGET ?? 150_000),
  });
  const client = new AnthropicClaudeClient({ budget });

  const planner = new Planner(client, {
    model: process.env.ANTHROPIC_PLANNER_MODEL ?? "claude-opus-4-7",
    retries: 1,
  });
  const integration = new IntegrationAgent({
    model: process.env.ANTHROPIC_EXECUTOR_MODEL ?? "claude-sonnet-4-6",
    registry,
    client,
  });
  const execution = new ExecutionAgent();
  const validation = new ValidationAgent(client, {
    model: process.env.ANTHROPIC_VALIDATOR_MODEL ?? "claude-sonnet-4-6",
  });
  const recovery = new RecoveryAgent(client, {
    model: process.env.ANTHROPIC_RECOVERY_MODEL ?? "claude-sonnet-4-6",
  });
  const security = new SecurityAgent(policy, approvals, client, {
    model: process.env.ANTHROPIC_VALIDATOR_MODEL ?? "claude-sonnet-4-6",
    explainWithLLM: true,
    escalateAmbiguous: false,
  });

  const deps: OrchestratorDeps = {
    tasks,
    steps,
    approvals,
    memory,
    registry,
    policy,
    traces,
    planner,
    client,
    budget,
    integration,
    execution,
    validation,
    recovery,
    security,
  };
  const orchestrator = new Orchestrator(deps);

  const app = express();
  app.use(express.json({ limit: "1mb" }));

  app.post("/v1/tasks", async (req, res, next) => {
    try {
      const task = await intakeTask(deps, { goal: req.body, priority: req.body.priority });
      res.json({ task });
    } catch (e) {
      next(e);
    }
  });

  app.post("/v1/tasks/:id/advance", async (req, res, next) => {
    try {
      const task = await orchestrator.executeToCompletion(req.params.id!);
      res.json({ task });
    } catch (e) {
      next(e);
    }
  });

  app.get("/v1/tasks/:id", async (req, res, next) => {
    try {
      const task = await tasks.get(req.params.id!);
      if (!task) return res.status(404).json({ error: "not found" });
      const stepsList = await steps.listForTask(task.id);
      const traceList = await traces.listForTask(task.id);
      const pendingApprovals = await approvals.listPending(task.id);
      res.json({ task, steps: stepsList, traces: traceList, pendingApprovals });
    } catch (e) {
      next(e);
    }
  });

  app.post("/v1/approvals/:id/respond", async (req, res, next) => {
    try {
      const { decision, by, reason } = req.body as {
        decision: "approved" | "rejected";
        by: string;
        reason?: string;
      };
      const task = await orchestrator.respondToApproval(
        req.params.id!,
        decision,
        by,
        reason,
      );
      res.json({ task });
    } catch (e) {
      next(e);
    }
  });

  app.get("/v1/connectors", (_req, res) => {
    res.json({ connectors: registry.list() });
  });

  app.post("/v1/connectors/health", async (_req, res) => {
    await registry.checkHealthAll();
    res.json({ connectors: registry.list() });
  });

  app.get("/v1/metrics", (_req, res) => {
    res.json(metrics.snapshot());
  });

  app.get("/healthz", (_req, res) => res.json({ ok: true }));

  app.use((err: unknown, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
    log.error({ err }, "request failed");
    const any = err as { code?: string; message?: string; details?: unknown };
    res.status(400).json({ error: { code: any.code ?? "UNKNOWN", message: any.message, details: any.details } });
  });

  return { app, orchestrator, deps };
}

export { boot };

// Auto-start the listener when this module is the process entrypoint, or when
// START_API=1 is set (useful under tsx --watch).
const invokedDirectly =
  typeof require !== "undefined" && require.main === module;
if (invokedDirectly || process.env.START_API === "1") {
  const { app } = boot();
  const port = Number(process.env.PORT ?? 8080);
  app.listen(port, () => log.info({ port }, "api listening"));
}
