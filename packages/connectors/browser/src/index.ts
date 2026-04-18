import type { Browser, BrowserContext, Page } from "playwright";
import {
  CapabilityDescriptor,
  ConnectorRequest,
  OrchestratorError,
  newConnectorId,
} from "@snipervrt/shared";
import { BaseConnector } from "@snipervrt/connector-core";
import { BrowserAgent, type BrowserGuidedStep } from "@snipervrt/agent-browser";
import type { ClaudeClient } from "@snipervrt/planner";

// Browser fallback connector. This is the LAST-RESORT transport. It is only
// chosen by the planner when no MCP/API/GraphQL option exists, and the
// registry gives it the lowest transport priority automatically.

export interface BrowserConnectorOptions {
  client: ClaudeClient;
  model: string;
  headless?: boolean;
  storageDir?: string;
  allowedHosts?: string[];
}

const CAPABILITIES: CapabilityDescriptor[] = [
  {
    name: "extract_page",
    description: "Navigate to a URL and extract fields from the resulting page.",
    actionType: "read",
    inputSchema: { url: "string", extractFields: "array" },
    outputSchema: { url: "string", extracted: "object" },
    riskLevel: "medium",
    sideEffects: false,
    idempotent: true,
    cost: { approxTokens: 2000, approxUsd: 0.01, approxLatencyMs: 6000 },
    examples: [{ url: "https://example.com", extractFields: ["h1", ".price"] }],
  },
  {
    name: "guided_flow",
    description: "Run a fixed sequence of browser actions (click/type/navigate/extract). Model is not in the loop.",
    actionType: "update",
    inputSchema: { startUrl: "string", steps: "array" },
    outputSchema: { finalUrl: "string", extracted: "object" },
    riskLevel: "high",
    sideEffects: true,
    idempotent: false,
    cost: { approxTokens: 0, approxUsd: 0, approxLatencyMs: 10_000 },
    examples: [],
  },
  {
    name: "adaptive_flow",
    description: "Goal-directed navigation. Model chooses one action per turn from a reduced page digest.",
    actionType: "update",
    inputSchema: { startUrl: "string", goal: "string", maxSteps: "number" },
    outputSchema: { finalUrl: "string", actions: "array", extracted: "object" },
    riskLevel: "high",
    sideEffects: true,
    idempotent: false,
    cost: { approxTokens: 8000, approxUsd: 0.05, approxLatencyMs: 20_000 },
    examples: [],
  },
];

export class BrowserConnector extends BaseConnector {
  private browser?: Browser;
  private context?: BrowserContext;
  private readonly agent: BrowserAgent;

  constructor(private readonly cfg: BrowserConnectorOptions) {
    super({
      id: newConnectorId(),
      serviceName: "browser",
      version: "1.0.0",
      transport: "browser",
      authKind: "none",
      capabilities: CAPABILITIES,
      riskLevel: "high",
      enabled: true,
      health: "unknown",
      tags: ["browser", "fallback"],
      description: "Playwright-backed browser fallback connector. Use only when no direct integration exists.",
    });
    this.agent = new BrowserAgent(cfg.client, { model: cfg.model });
  }

  async close(): Promise<void> {
    await this.context?.close();
    await this.browser?.close();
    this.context = undefined;
    this.browser = undefined;
  }

  private async page(): Promise<Page> {
    if (!this.browser) {
      const { chromium } = await import("playwright");
      this.browser = await chromium.launch({ headless: this.cfg.headless ?? true });
      this.context = await this.browser.newContext({
        storageState: this.cfg.storageDir ? `${this.cfg.storageDir}/state.json` : undefined,
      });
    }
    const page = await this.context!.newPage();
    return page;
  }

  private assertHostAllowed(url: string) {
    if (!this.cfg.allowedHosts || this.cfg.allowedHosts.length === 0) return;
    const host = new URL(url).host;
    if (!this.cfg.allowedHosts.some((h) => host.endsWith(h))) {
      throw new OrchestratorError("POLICY_DENIED", `host ${host} not in allowlist`);
    }
  }

  protected async invoke(cap: CapabilityDescriptor, req: ConnectorRequest): Promise<unknown> {
    const url = String(req.input.url ?? req.input.startUrl ?? "");
    if (url) this.assertHostAllowed(url);

    const page = await this.page();
    try {
      switch (cap.name) {
        case "extract_page": {
          await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30_000 });
          const fields = (req.input.extractFields as string[]) ?? [];
          const extracted: Record<string, unknown> = {};
          for (const sel of fields) {
            try {
              extracted[sel] = (await page.locator(sel).first().textContent()) ?? null;
            } catch {
              extracted[sel] = null;
            }
          }
          return { url: page.url(), extracted };
        }
        case "guided_flow": {
          await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30_000 });
          const steps = (req.input.steps as BrowserGuidedStep[]) ?? [];
          const r = await this.agent.run({ page, goal: "", guided: steps });
          return { finalUrl: r.finalUrl, extracted: r.extracted, actions: r.actions, aborted: r.aborted };
        }
        case "adaptive_flow": {
          await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30_000 });
          const goal = String(req.input.goal ?? "");
          const maxSteps = Number(req.input.maxSteps ?? 10);
          const r = await this.agent.run({ page, goal, maxSteps });
          return { finalUrl: r.finalUrl, actions: r.actions, extracted: r.extracted, aborted: r.aborted };
        }
        default:
          throw new OrchestratorError("CONNECTOR_NOT_FOUND", `browser: unknown ${cap.name}`);
      }
    } finally {
      await page.close();
    }
  }

  protected override async probe(): Promise<void> {
    const page = await this.page();
    await page.close();
  }
}
