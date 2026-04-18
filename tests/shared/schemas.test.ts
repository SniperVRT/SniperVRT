import { describe, it, expect } from "vitest";
import {
  PlanSchema,
  GoalSchema,
  TaskStatus,
  ConnectorDescriptorSchema,
  PolicyBundleSchema,
  MemoryEntrySchema,
  ApprovalRequestSchema,
  TraceSchema,
  DEFAULT_ACTION_RISK,
  maxRisk,
  newTaskId,
  OrchestratorError,
  asOrchestratorError,
} from "../../packages/shared/src/index.js";

describe("shared schemas", () => {
  it("parses a well-formed goal", () => {
    const g = GoalSchema.parse({ objective: "Summarize my inbox" });
    expect(g.mode).toBe("normal");
    expect(g.constraints).toEqual([]);
  });

  it("rejects an empty plan", () => {
    const bad = PlanSchema.safeParse({ objective: "x", summary: "y", steps: [] });
    expect(bad.success).toBe(false);
  });

  it("accepts a minimal plan and applies defaults", () => {
    const p = PlanSchema.parse({
      objective: "Send a nudge email",
      summary: "Draft + send",
      steps: [
        {
          id: "s1",
          description: "Draft it",
          actionType: "draft",
          successCondition: "Draft exists",
        },
      ],
    });
    expect(p.version).toBe(1);
    expect(p.steps[0]?.confidence).toBe(0.7);
    expect(p.steps[0]?.retryStrategy.maxAttempts).toBe(2);
  });

  it("risk ordering works", () => {
    expect(maxRisk("low", "high", "medium")).toBe("high");
    expect(DEFAULT_ACTION_RISK.send).toBe("high");
    expect(DEFAULT_ACTION_RISK.read).toBe("low");
  });

  it("TaskStatus enum includes the full lifecycle", () => {
    for (const s of [
      "pending",
      "planning",
      "running",
      "awaiting_approval",
      "succeeded",
      "failed",
    ] as const) {
      expect(TaskStatus.parse(s)).toBe(s);
    }
  });

  it("ids are k-sortable and prefixed", async () => {
    const a = newTaskId();
    await new Promise((r) => setTimeout(r, 2));
    const b = newTaskId();
    expect(a.startsWith("tsk_")).toBe(true);
    expect(a < b).toBe(true);
  });

  it("OrchestratorError keeps code + retryable flag", () => {
    const e = new OrchestratorError("CONNECTOR_RATE_LIMITED", "slow down");
    expect(e.retryable).toBe(true);
    const n = asOrchestratorError(new Error("boom"));
    expect(n.code).toBe("UNKNOWN");
  });

  it("connector/policy/memory/approval/trace schemas accept minimal valid input", () => {
    ConnectorDescriptorSchema.parse({
      id: "cxn_1",
      serviceName: "gmail",
      transport: "rest",
      authKind: "oauth2",
      capabilities: [],
    });
    PolicyBundleSchema.parse({ rules: [] });
    MemoryEntrySchema.parse({
      id: "mem_1",
      kind: "workflow_template",
      scope: "global",
      title: "x",
      body: "y",
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    });
    ApprovalRequestSchema.parse({
      id: "apv_1",
      taskId: "tsk_1",
      stepId: "stp_1",
      title: "t",
      humanSummary: "s",
      actionType: "send",
      connector: "gmail",
      riskLevel: "high",
      intent: "send an email",
      requestedAt: new Date().toISOString(),
    });
    TraceSchema.parse({
      id: "trc_1",
      taskId: "tsk_1",
      kind: "note",
      summary: "hi",
      timestamp: new Date().toISOString(),
    });
  });
});
