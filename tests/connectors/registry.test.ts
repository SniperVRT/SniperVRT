import { describe, it, expect } from "vitest";
import { ConnectorRegistry, BaseConnector } from "../../packages/connectors/core/src/index.js";
import type { ConnectorDescriptor, CapabilityDescriptor, ConnectorRequest } from "../../packages/shared/src/index.js";

function makeDescriptor(
  id: string,
  serviceName: string,
  transport: "mcp" | "rest" | "browser",
  caps: Partial<CapabilityDescriptor>[] = [],
): ConnectorDescriptor {
  return {
    id,
    serviceName,
    version: "1.0.0",
    transport,
    authKind: "api_key",
    capabilities: caps.map((c, i) => ({
      name: c.name ?? `cap${i}`,
      description: c.description ?? "",
      actionType: c.actionType ?? "read",
      inputSchema: {},
      outputSchema: {},
      riskLevel: c.riskLevel ?? "low",
      sideEffects: false,
      idempotent: true,
      cost: { approxTokens: 0, approxUsd: 0, approxLatencyMs: 100 },
      examples: [],
    })),
    riskLevel: "low",
    enabled: true,
    health: "healthy",
    tags: [],
    description: "",
  };
}

class Fake extends BaseConnector {
  protected async invoke(_c: CapabilityDescriptor, _r: ConnectorRequest) {
    return { ok: true };
  }
}

describe("ConnectorRegistry", () => {
  it("prefers MCP transport over REST over browser", () => {
    const r = new ConnectorRegistry();
    r.register(
      new Fake(makeDescriptor("a", "svc", "browser", [{ name: "c", actionType: "read" }])),
    );
    r.register(
      new Fake(makeDescriptor("b", "svc", "rest", [{ name: "c", actionType: "read" }])),
    );
    r.register(
      new Fake(makeDescriptor("c", "svc", "mcp", [{ name: "c", actionType: "read" }])),
    );
    const ranked = r.select({ capabilityName: "c" });
    expect(ranked[0]?.connector.descriptor.transport).toBe("mcp");
    expect(ranked[1]?.connector.descriptor.transport).toBe("rest");
    expect(ranked[2]?.connector.descriptor.transport).toBe("browser");
  });

  it("boosts preferred service and excludes forbidden ones", () => {
    const r = new ConnectorRegistry();
    r.register(new Fake(makeDescriptor("g", "gmail", "rest", [{ name: "send" }])));
    r.register(new Fake(makeDescriptor("o", "outlook", "rest", [{ name: "send" }])));
    const ranked = r.select({
      preferredService: "outlook",
      capabilityName: "send",
      forbiddenServices: ["gmail"],
    });
    expect(ranked.length).toBe(1);
    expect(ranked[0]?.connector.descriptor.serviceName).toBe("outlook");
  });

  it("dry-run short-circuits execute() without invoking", async () => {
    let invoked = false;
    class Spy extends BaseConnector {
      protected async invoke() {
        invoked = true;
        return {};
      }
    }
    const c = new Spy(makeDescriptor("s", "svc", "rest", [{ name: "x", actionType: "send" }]));
    const res = await c.execute({ capability: "x", input: {}, dryRun: true });
    expect(res.ok).toBe(true);
    expect(res.meta.dryRun).toBe(true);
    expect(invoked).toBe(false);
  });

  it("idempotency ledger replays prior response", async () => {
    let calls = 0;
    class Once extends BaseConnector {
      protected async invoke() {
        calls++;
        return { n: calls };
      }
    }
    const c = new Once(makeDescriptor("s", "svc", "rest", [{ name: "x", actionType: "create" }]));
    const a = await c.execute({ capability: "x", input: {}, idempotencyKey: "k1", dryRun: false });
    const b = await c.execute({ capability: "x", input: {}, idempotencyKey: "k1", dryRun: false });
    expect(a).toBe(b);
    expect(calls).toBe(1);
  });
});
