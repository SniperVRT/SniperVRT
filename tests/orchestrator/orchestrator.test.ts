import { describe, it, expect } from "vitest";
import {
  BaseConnector,
  ConnectorRegistry,
} from "../../packages/connectors/core/src/index.js";
import type {
  CapabilityDescriptor,
  ConnectorDescriptor,
  ConnectorRequest,
} from "../../packages/shared/src/index.js";
import {
  newConnectorId,
  GoalSchema,
  OrchestratorError,
} from "../../packages/shared/src/index.js";
import {
  InMemoryTaskStore,
  InMemoryStepStore,
} from "../../packages/state/src/index.js";
import {
  InMemoryApprovalStore,
  PolicyEngine,
  DEFAULT_POLICY_BUNDLE,
} from "../../packages/policies/src/index.js";
import { InMemoryMemoryStore } from "../../packages/memory/src/index.js";
import { InMemoryTraceRecorder } from "../../packages/telemetry/src/index.js";
import {
  ScriptedClaudeClient,
  Planner,
  Budget,
} from "../../packages/planner/src/index.js";
import { IntegrationAgent } from "../../packages/agents/integration/src/index.js";
import { ExecutionAgent } from "../../packages/agents/execution/src/index.js";
import { ValidationAgent } from "../../packages/agents/validation/src/index.js";
import { RecoveryAgent } from "../../packages/agents/recovery/src/index.js";
import { SecurityAgent } from "../../packages/agents/security/src/index.js";
import {
  Orchestrator,
  intakeTask,
  type OrchestratorDeps,
} from "../../packages/orchestrator/src/index.js";

// ----------------------------------------------------------------------------
// A fake connector wired through BaseConnector. Each instance is configurable:
// - which capability name/actionType/risk
// - what output shape invoke() returns
// - whether invoke() should throw (for recovery tests)
// ----------------------------------------------------------------------------
class FakeConnector extends BaseConnector {
  callCount = 0;
  constructor(
    descriptor: ConnectorDescriptor,
    private readonly behavior: (req: ConnectorRequest) => Promise<unknown> | unknown,
  ) {
    super(descriptor);
  }
  protected async invoke(_cap: CapabilityDescriptor, req: ConnectorRequest): Promise<unknown> {
    this.callCount++;
    return await this.behavior(req);
  }
  protected override async probe(): Promise<void> {}
}

function descriptorFor(
  serviceName: string,
  capName: string,
  actionType: CapabilityDescriptor["actionType"],
  risk: CapabilityDescriptor["riskLevel"] = "low",
): ConnectorDescriptor {
  return {
    id: newConnectorId(),
    serviceName,
    version: "1.0.0",
    transport: "rest",
    authKind: "api_key",
    capabilities: [
      {
        name: capName,
        description: `${serviceName}.${capName}`,
        actionType,
        inputSchema: {},
        outputSchema: {},
        riskLevel: risk,
        sideEffects: actionType !== "read" && actionType !== "list",
        idempotent: actionType === "read" || actionType === "list",
        cost: { approxTokens: 0, approxUsd: 0, approxLatencyMs: 50 },
        examples: [],
      },
    ],
    riskLevel: risk,
    enabled: true,
    health: "healthy",
    tags: [],
    description: `fake ${serviceName}`,
  };
}

// A self-contained rig: returns everything wired together so tests can poke at
// internals (memory, traces, approvals) after advancing a task.
function makeRig(scripts: Map<string, (bundle: unknown) => unknown>) {
  const registry = new ConnectorRegistry();
  const tasks = new InMemoryTaskStore();
  const steps = new InMemoryStepStore();
  const approvals = new InMemoryApprovalStore();
  const memory = new InMemoryMemoryStore();
  const traces = new InMemoryTraceRecorder();
  const policy = new PolicyEngine(DEFAULT_POLICY_BUNDLE);
  const budget = new Budget({ maxTokens: 1_000_000 });
  const client = new ScriptedClaudeClient(scripts as never, budget);
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
  const orch = new Orchestrator(deps);
  return { deps, orch, registry, tasks, steps, approvals, traces, memory };
}

// A minimal planner reply for a 1-step low-risk read plan. The success condition
// matches the ValidationAgent's fastCheck so no validator LLM call is needed.
function oneStepLowRiskPlan(serviceName: string) {
  return {
    version: 1,
    objective: "demo",
    summary: "read a thing",
    assumptions: [],
    constraints: [],
    requiredServices: [serviceName],
    optionalServices: [],
    steps: [
      {
        id: "s1",
        description: `read from ${serviceName}`,
        assignedAgent: "execution",
        preferredConnector: serviceName,
        candidateConnectors: [serviceName],
        actionType: "read",
        riskLevel: "low",
        dependsOn: [],
        inputTemplate: { q: "anything" },
        successCondition: "output.ok is true",
        confidence: 0.9,
      },
    ],
    overallRisk: "low",
    confidence: 0.9,
  };
}

describe("Orchestrator end-to-end", () => {
  it("runs a one-step low-risk task from pending -> succeeded", async () => {
    const scripts = new Map<string, (bundle: unknown) => unknown>([
      ["planner.v1.2", () => oneStepLowRiskPlan("demo")],
    ]);
    const rig = makeRig(scripts);
    const connector = new FakeConnector(
      descriptorFor("demo", "read_thing", "read", "low"),
      async () => ({ ok: true, items: [1, 2, 3] }),
    );
    rig.registry.register(connector);

    const goal = GoalSchema.parse({ objective: "read from demo" });
    const task = await intakeTask(rig.deps, { goal });
    const final = await rig.orch.executeToCompletion(task.id);

    expect(final.status).toBe("succeeded");
    expect(final.result?.summary).toMatch(/completed with/);
    expect(connector.callCount).toBe(1);
    const stepRows = await rig.steps.listForTask(task.id);
    expect(stepRows.length).toBe(1);
    expect(stepRows[0]?.status).toBe("succeeded");
    expect(stepRows[0]?.outputPayload).toMatchObject({ ok: true });

    const traces = await rig.traces.listForTask(task.id);
    const kinds = traces.map((t) => t.kind);
    expect(kinds).toContain("goal_received");
    expect(kinds).toContain("plan_generated");
    expect(kinds).toContain("policy_evaluated");
    expect(kinds).toContain("step_started");
    expect(kinds).toContain("validation_performed");
    expect(kinds).toContain("step_succeeded");
    expect(kinds).toContain("task_succeeded");
  });

  it("honors dry-run mode without invoking connectors", async () => {
    // Dry-run: use a success condition that matches the describeDryRun preview
    // so validation passes. Connector.invoke() must not be called.
    const dryPlan = {
      ...oneStepLowRiskPlan("demo"),
      steps: [
        {
          ...oneStepLowRiskPlan("demo").steps[0],
          successCondition: "output.items is an array",
        },
      ],
    };
    // describeDryRun returns { dryRun, service, capability, inputHash } - no
    // items array - so we use a trivial successCondition and explicitly a
    // recovery script returning abort to land the task in "failed".
    const scripts = new Map<string, (bundle: unknown) => unknown>([
      ["planner.v1.2", () => dryPlan],
      [
        "recovery.v1.1",
        () => ({
          decision: "abort",
          alternateConnector: null,
          reason: "dry-run preview failed validation as expected",
          confidence: 1,
          patchHint: null,
        }),
      ],
    ]);
    const rig = makeRig(scripts);
    const connector = new FakeConnector(
      descriptorFor("demo", "read_thing", "read", "low"),
      async () => ({ ok: true }),
    );
    rig.registry.register(connector);

    const goal = GoalSchema.parse({ objective: "read in dry-run", mode: "dry-run" });
    const task = await intakeTask(rig.deps, { goal, mode: "dry-run" });
    const final = await rig.orch.executeToCompletion(task.id);

    expect(final.status).toBe("failed");
    // The critical invariant: dry-run must never call the real connector.
    expect(connector.callCount).toBe(0);
  });

  it("requires approval when planner emits a high-risk send step", async () => {
    const scripts = new Map<string, (bundle: unknown) => unknown>([
      [
        "planner.v1.2",
        () => ({
          version: 1,
          objective: "send a message",
          summary: "one risky send",
          assumptions: [],
          constraints: [],
          requiredServices: ["mailer"],
          optionalServices: [],
          steps: [
            {
              id: "s1",
              description: "send hello",
              assignedAgent: "execution",
              preferredConnector: "mailer",
              candidateConnectors: ["mailer"],
              actionType: "send",
              riskLevel: "high",
              dependsOn: [],
              inputTemplate: { to: "user@example.com", subject: "hi", body: "hi" },
              successCondition: "output.ok is true",
              confidence: 0.9,
            },
          ],
          overallRisk: "high",
          confidence: 0.9,
        }),
      ],
    ]);
    const rig = makeRig(scripts);
    const connector = new FakeConnector(
      descriptorFor("mailer", "send_message", "send", "high"),
      async () => ({ ok: true, id: "msg_1" }),
    );
    rig.registry.register(connector);

    const goal = GoalSchema.parse({ objective: "send hello" });
    const task = await intakeTask(rig.deps, { goal });
    const paused = await rig.orch.executeToCompletion(task.id);

    expect(paused.status).toBe("awaiting_approval");
    const pending = await rig.approvals.listPending(task.id);
    expect(pending.length).toBe(1);
    expect(pending[0]?.riskLevel).toBe("high");
    expect(connector.callCount).toBe(0);

    // Approve the request; the task should resume and succeed.
    const resumed = await rig.orch.respondToApproval(
      pending[0]!.id,
      "approved",
      "reviewer@example.com",
      "ok",
    );
    expect(["running", "succeeded"]).toContain(resumed.status);
    const final = await rig.orch.executeToCompletion(task.id);
    expect(final.status).toBe("succeeded");
    expect(connector.callCount).toBe(1);
  });

  it("retries transient upstream errors via the recovery fast-path", async () => {
    const scripts = new Map<string, (bundle: unknown) => unknown>([
      ["planner.v1.2", () => oneStepLowRiskPlan("flaky")],
    ]);
    const rig = makeRig(scripts);
    let attempts = 0;
    const connector = new FakeConnector(
      descriptorFor("flaky", "read_thing", "read", "low"),
      async () => {
        attempts++;
        if (attempts < 2) {
          throw new OrchestratorError("CONNECTOR_UPSTREAM_ERROR", "upstream 502", {
            retryable: true,
          });
        }
        return { ok: true };
      },
    );
    rig.registry.register(connector);

    const goal = GoalSchema.parse({ objective: "read flaky" });
    const task = await intakeTask(rig.deps, { goal });
    const final = await rig.orch.executeToCompletion(task.id);
    expect(final.status).toBe("succeeded");
    expect(attempts).toBeGreaterThanOrEqual(2);
  });

  it("fails cleanly when no connector can satisfy the plan", async () => {
    const scripts = new Map<string, (bundle: unknown) => unknown>([
      ["planner.v1.2", () => oneStepLowRiskPlan("missing")],
    ]);
    const rig = makeRig(scripts); // no connectors registered
    const goal = GoalSchema.parse({ objective: "read missing" });
    const task = await intakeTask(rig.deps, { goal });
    await expect(rig.orch.executeToCompletion(task.id)).rejects.toThrow();
  });
});
