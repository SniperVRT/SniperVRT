/**
 * End-to-end sample: runs a full task lifecycle against fully in-memory
 * backends and a scripted Claude client. No external services are required.
 *
 * Run with:
 *   npm run example
 *   (= tsx examples/run-sample-task.ts)
 *
 * Demonstrates:
 *   - intake -> planning -> ready -> running -> succeeded
 *   - registry selection of a deterministic connector
 *   - policy allow for a low-risk read step
 *   - validation via the fast deterministic pass (no LLM call)
 *   - traces + final result aggregation
 */
import {
  BaseConnector,
  ConnectorRegistry,
} from "../packages/connectors/core/src/index.js";
import {
  GoalSchema,
  newConnectorId,
  type CapabilityDescriptor,
  type ConnectorDescriptor,
  type ConnectorRequest,
} from "../packages/shared/src/index.js";
import {
  InMemoryTaskStore,
  InMemoryStepStore,
} from "../packages/state/src/index.js";
import {
  InMemoryApprovalStore,
  PolicyEngine,
  DEFAULT_POLICY_BUNDLE,
} from "../packages/policies/src/index.js";
import { InMemoryMemoryStore } from "../packages/memory/src/index.js";
import { InMemoryTraceRecorder } from "../packages/telemetry/src/index.js";
import {
  ScriptedClaudeClient,
  Planner,
  Budget,
} from "../packages/planner/src/index.js";
import { IntegrationAgent } from "../packages/agents/integration/src/index.js";
import { ExecutionAgent } from "../packages/agents/execution/src/index.js";
import { ValidationAgent } from "../packages/agents/validation/src/index.js";
import { RecoveryAgent } from "../packages/agents/recovery/src/index.js";
import { SecurityAgent } from "../packages/agents/security/src/index.js";
import {
  Orchestrator,
  intakeTask,
  type OrchestratorDeps,
} from "../packages/orchestrator/src/index.js";

class DemoNotesConnector extends BaseConnector {
  protected async invoke(_cap: CapabilityDescriptor, req: ConnectorRequest): Promise<unknown> {
    // Pretend we fetched notes matching the query.
    const q = String(req.input.query ?? "");
    return {
      ok: true,
      items: [
        { id: "note_1", title: `Match for ${q}`, snippet: "..." },
        { id: "note_2", title: "Weekly review", snippet: "..." },
      ],
    };
  }
}

function buildDemoConnector(): DemoNotesConnector {
  const descriptor: ConnectorDescriptor = {
    id: newConnectorId(),
    serviceName: "demo_notes",
    version: "1.0.0",
    transport: "rest",
    authKind: "api_key",
    capabilities: [
      {
        name: "search_notes",
        description: "Search notes by keyword.",
        actionType: "search",
        inputSchema: { query: "string" },
        outputSchema: { items: "array" },
        riskLevel: "low",
        sideEffects: false,
        idempotent: true,
        cost: { approxTokens: 0, approxUsd: 0, approxLatencyMs: 50 },
        examples: [{ query: "weekly review" }],
      },
    ],
    riskLevel: "low",
    enabled: true,
    health: "healthy",
    tags: ["demo"],
    description: "Fake notes service for the end-to-end example.",
  };
  return new DemoNotesConnector(descriptor);
}

async function main(): Promise<void> {
  // --- Wiring (all in-memory) ---------------------------------------------
  const registry = new ConnectorRegistry();
  registry.register(buildDemoConnector());

  const tasks = new InMemoryTaskStore();
  const steps = new InMemoryStepStore();
  const approvals = new InMemoryApprovalStore();
  const memory = new InMemoryMemoryStore();
  const traces = new InMemoryTraceRecorder();
  const policy = new PolicyEngine(DEFAULT_POLICY_BUNDLE);
  const budget = new Budget({ maxTokens: 500_000 });

  // Scripted Claude: returns a deterministic 1-step low-risk plan.
  const client = new ScriptedClaudeClient(
    new Map([
      [
        "planner.v1.2",
        () => ({
          version: 1,
          objective: "Find notes about weekly review",
          summary: "search notes then surface titles",
          assumptions: [],
          constraints: [],
          requiredServices: ["demo_notes"],
          optionalServices: [],
          steps: [
            {
              id: "s1",
              description: "Search notes for 'weekly review'",
              assignedAgent: "execution",
              preferredConnector: "demo_notes",
              candidateConnectors: ["demo_notes"],
              actionType: "search",
              riskLevel: "low",
              dependsOn: [],
              inputTemplate: { query: "weekly review" },
              successCondition: "output.items is an array",
              confidence: 0.9,
            },
          ],
          overallRisk: "low",
          confidence: 0.9,
        }),
      ],
    ]),
    budget,
  );

  const planner = new Planner(client, { model: "claude-opus-4-7", retries: 0 });
  const integration = new IntegrationAgent({ model: "claude-sonnet-4-6", registry, client });
  const execution = new ExecutionAgent();
  const validation = new ValidationAgent(client, { model: "claude-sonnet-4-6" });
  const recovery = new RecoveryAgent(client, { model: "claude-sonnet-4-6" });
  const security = new SecurityAgent(policy, approvals, client, {
    model: "claude-sonnet-4-6",
    explainWithLLM: false,
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

  // --- Run ------------------------------------------------------------------
  const goal = GoalSchema.parse({
    objective: "Find notes about weekly review",
    preferredServices: ["demo_notes"],
    successCriteria: ["at least one note surfaced"],
  });
  const task = await intakeTask(deps, { goal });
  console.log(`[intake] task ${task.id} status=${task.status}`);

  const finished = await orchestrator.executeToCompletion(task.id);
  console.log(`[done] task ${finished.id} status=${finished.status}`);

  const stepRows = await steps.listForTask(task.id);
  for (const s of stepRows) {
    console.log(`  step ${s.planStepId}: ${s.status} via ${s.connector}`);
    console.log(`    output: ${JSON.stringify(s.outputPayload).slice(0, 200)}`);
  }
  const traceRows = await traces.listForTask(task.id);
  console.log(`\n[trace] ${traceRows.length} entries:`);
  for (const t of traceRows) {
    console.log(`  ${t.timestamp} ${t.kind} [${t.status}] ${t.summary}`);
  }
  console.log(`\n[result] ${JSON.stringify(finished.result, null, 2)}`);
  const snap = budget.snapshot();
  console.log(`[budget] tokens=${snap.tokensUsed} usd=${snap.usdSpent.toFixed(4)}`);
}

main().catch((e) => {
  console.error("example crashed", e);
  process.exit(1);
});
