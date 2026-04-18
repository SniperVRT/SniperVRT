import type { Page } from "playwright";
import type { ClaudeClient } from "@snipervrt/planner";
import { browserNextActionPrompt } from "@snipervrt/prompts";
import { OrchestratorError } from "@snipervrt/shared";
import { buildPageDigest } from "./digest.js";
import type { BrowserAction, BrowserGuidedStep } from "./types.js";

export interface BrowserRunInput {
  page: Page;
  goal: string;
  maxSteps?: number;
  // Guided mode runs a fixed action sequence without asking the model.
  // Leave empty for adaptive mode.
  guided?: BrowserGuidedStep[];
  onAction?: (action: BrowserAction, stepIndex: number) => void;
  approve?: (action: BrowserAction) => Promise<boolean>;
}

export interface BrowserRunResult {
  finalUrl: string;
  extracted: Record<string, unknown>;
  actions: BrowserAction[];
  reason: string;
  aborted: boolean;
}

// Guided mode = deterministic. Adaptive mode = model-in-the-loop, one action
// per turn, each turn sees only a page digest (never the raw DOM).
export class BrowserAgent {
  constructor(
    private readonly client: ClaudeClient,
    private readonly opts: { model: string },
  ) {}

  async run(input: BrowserRunInput): Promise<BrowserRunResult> {
    if (input.guided && input.guided.length > 0) return this.runGuided(input);
    return this.runAdaptive(input);
  }

  private async runGuided(input: BrowserRunInput): Promise<BrowserRunResult> {
    const actions: BrowserAction[] = [];
    const extracted: Record<string, unknown> = {};
    for (let i = 0; i < input.guided!.length; i++) {
      const step = input.guided![i]!;
      if (step.requiresApproval && input.approve) {
        const ok = await input.approve(step);
        if (!ok) return { finalUrl: input.page.url(), extracted, actions, reason: "approval rejected", aborted: true };
      }
      await this.executeAction(input.page, step, extracted);
      input.onAction?.(step, i);
      actions.push(step);
    }
    return { finalUrl: input.page.url(), extracted, actions, reason: "guided complete", aborted: false };
  }

  private async runAdaptive(input: BrowserRunInput): Promise<BrowserRunResult> {
    const actions: BrowserAction[] = [];
    const extracted: Record<string, unknown> = {};
    const priorActions: string[] = [];
    const max = input.maxSteps ?? 10;

    for (let i = 0; i < max; i++) {
      const digest = await buildPageDigest(input.page);
      const { data } = await this.client.callJson<BrowserAction>(this.opts.model, browserNextActionPrompt({
        goal: input.goal,
        pageUrl: digest.url,
        pageDigest: `${digest.title}\nActionables:\n${digest.actionables}\n\nText:\n${digest.textDigest}`,
        priorActions,
      }));
      const action = normalizeAction(data);
      actions.push(action);
      priorActions.push(`${action.kind}${action.targetSelector ? " " + action.targetSelector : ""}${action.url ? " " + action.url : ""}`);
      input.onAction?.(action, i);

      if (action.requiresApproval && input.approve) {
        const ok = await input.approve(action);
        if (!ok) return { finalUrl: input.page.url(), extracted, actions, reason: "approval rejected", aborted: true };
      }
      if (action.kind === "done") {
        return { finalUrl: input.page.url(), extracted, actions, reason: action.reason, aborted: false };
      }
      if (action.kind === "abort") {
        return { finalUrl: input.page.url(), extracted, actions, reason: action.reason, aborted: true };
      }
      await this.executeAction(input.page, action, extracted);
    }
    return { finalUrl: input.page.url(), extracted, actions, reason: "max steps reached", aborted: true };
  }

  private async executeAction(
    page: Page,
    action: BrowserAction,
    extracted: Record<string, unknown>,
  ): Promise<void> {
    switch (action.kind) {
      case "navigate":
        if (!action.url) throw new OrchestratorError("VALIDATION_FAILED", "navigate needs url");
        await page.goto(action.url, { waitUntil: "domcontentloaded" });
        return;
      case "click":
        if (!action.targetSelector) throw new OrchestratorError("VALIDATION_FAILED", "click needs selector");
        await page.click(action.targetSelector, { timeout: 10_000 });
        return;
      case "type":
        if (!action.targetSelector || action.text === undefined || action.text === null) {
          throw new OrchestratorError("VALIDATION_FAILED", "type needs selector + text");
        }
        await page.fill(action.targetSelector, action.text);
        return;
      case "wait":
        if (action.targetSelector) {
          await page.waitForSelector(action.targetSelector, { timeout: 10_000 });
        } else {
          await page.waitForLoadState("networkidle", { timeout: 10_000 });
        }
        return;
      case "extract":
        if (action.extractFields) {
          for (const f of action.extractFields) {
            try {
              extracted[f] = await page.locator(f).first().textContent();
            } catch {
              extracted[f] = null;
            }
          }
        }
        return;
      case "read":
      case "done":
      case "abort":
        return;
    }
  }
}

function normalizeAction(raw: Partial<BrowserAction>): BrowserAction {
  return {
    kind: (raw.kind as BrowserAction["kind"]) ?? "abort",
    targetSelector: raw.targetSelector ?? null,
    text: raw.text ?? null,
    url: raw.url ?? null,
    extractFields: raw.extractFields ?? [],
    requiresApproval: Boolean(raw.requiresApproval),
    reason: raw.reason ?? "",
    confidence: raw.confidence ?? 0.5,
  };
}
