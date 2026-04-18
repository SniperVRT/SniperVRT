import { describe, it, expect } from "vitest";
import { PolicyEngine, DEFAULT_POLICY_BUNDLE, InMemoryApprovalStore } from "../../packages/policies/src/index.js";

describe("PolicyEngine", () => {
  const engine = new PolicyEngine(DEFAULT_POLICY_BUNDLE);

  it("allows reads at low risk", () => {
    const d = engine.evaluate({
      actionType: "read",
      service: "gmail",
      capability: "list_messages",
      riskLevel: "low",
      mode: "normal",
    });
    expect(d.outcome).toBe("allow");
  });

  it("requires approval for purchases regardless of risk", () => {
    const d = engine.evaluate({
      actionType: "purchase",
      service: "shop",
      capability: "buy",
      riskLevel: "low",
      mode: "normal",
    });
    expect(d.outcome).toBe("require_approval");
  });

  it("denies authenticate actions", () => {
    const d = engine.evaluate({
      actionType: "authenticate",
      service: "auth",
      capability: "rotate",
      riskLevel: "critical",
      mode: "normal",
    });
    expect(d.outcome).toBe("deny");
  });

  it("dry-run bypasses approval rules", () => {
    const d = engine.evaluate({
      actionType: "purchase",
      service: "shop",
      capability: "buy",
      riskLevel: "critical",
      mode: "dry-run",
    });
    expect(d.outcome).toBe("allow");
  });

  it("applies the approval floor when no rule matches", () => {
    const d = engine.evaluate({
      actionType: "export",
      service: "internal",
      capability: "dump",
      riskLevel: "high",
      mode: "normal",
    });
    expect(d.outcome).toBe("require_approval");
  });
});

describe("Approval store", () => {
  it("round-trips a request lifecycle", async () => {
    const s = new InMemoryApprovalStore();
    const a = await s.create({
      taskId: "tsk_1",
      stepId: "stp_1",
      title: "Send email",
      humanSummary: "To team@example.com, subject X",
      intent: "send",
      actionType: "send",
      connector: "gmail",
      riskLevel: "high",
      effects: ["email will be sent"],
    });
    expect(a.status).toBe("pending");
    const ok = await s.respond(a.id, "approved", "user:alice");
    expect(ok.status).toBe("approved");
    await expect(s.respond(a.id, "rejected", "user:bob")).rejects.toThrow();
  });

  it("expires pending requests past their ttl", async () => {
    const s = new InMemoryApprovalStore();
    const a = await s.create({
      taskId: "t",
      stepId: "s",
      title: "x",
      humanSummary: "y",
      intent: "z",
      actionType: "send",
      connector: null,
      riskLevel: "high",
      ttlMs: -1,
    });
    const n = await s.expireOlderThan(new Date().toISOString());
    expect(n).toBe(1);
    const got = await s.get(a.id);
    expect(got?.status).toBe("expired");
  });
});
