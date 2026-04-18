import { OrchestratorError } from "@snipervrt/shared";

// Simple token/cost accountant. Orchestrator attaches one instance per task
// and one per tenant (if multi-tenant). Any Claude call must go through it.
export interface BudgetLimits {
  maxTokens?: number;
  maxUsd?: number;
}

export class Budget {
  private tokensUsed = 0;
  private usdSpent = 0;
  constructor(private readonly limits: BudgetLimits = {}) {}

  record(tokens: number, usd: number): void {
    this.tokensUsed += Math.max(0, tokens | 0);
    this.usdSpent += Math.max(0, usd);
    this.assertWithinLimits();
  }

  snapshot() {
    return {
      tokensUsed: this.tokensUsed,
      usdSpent: this.usdSpent,
      limits: { ...this.limits },
    };
  }

  assertWithinLimits() {
    if (this.limits.maxTokens && this.tokensUsed > this.limits.maxTokens) {
      throw new OrchestratorError("BUDGET_EXCEEDED", "token budget exceeded", {
        details: this.snapshot(),
      });
    }
    if (this.limits.maxUsd && this.usdSpent > this.limits.maxUsd) {
      throw new OrchestratorError("BUDGET_EXCEEDED", "usd budget exceeded", {
        details: this.snapshot(),
      });
    }
  }
}

// Rough tokens-to-usd mapping. Caller can override if they have exact prices.
// These are conservative defaults, not official pricing.
const PRICE_PER_M_INPUT_USD: Record<string, number> = {
  "claude-opus-4-7": 15,
  "claude-sonnet-4-6": 3,
  "claude-haiku-4-5-20251001": 0.8,
};
const PRICE_PER_M_OUTPUT_USD: Record<string, number> = {
  "claude-opus-4-7": 75,
  "claude-sonnet-4-6": 15,
  "claude-haiku-4-5-20251001": 4,
};

export function estimateUsd(model: string, inputTokens: number, outputTokens: number): number {
  const inP = PRICE_PER_M_INPUT_USD[model] ?? 3;
  const outP = PRICE_PER_M_OUTPUT_USD[model] ?? 15;
  return (inputTokens / 1_000_000) * inP + (outputTokens / 1_000_000) * outP;
}
