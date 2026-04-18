import { recoveryPrompt } from "@snipervrt/prompts";
import type { ClaudeClient } from "@snipervrt/planner";

export type RecoveryDecision =
  | "retry_same"
  | "retry_alt_connector"
  | "patch_plan"
  | "escalate"
  | "abort";

export interface RecoveryOutput {
  decision: RecoveryDecision;
  alternateConnector: string | null;
  reason: string;
  confidence: number;
  patchHint: string | null;
}

export interface RecoveryInput {
  stepDescription: string;
  failureClass: string; // OrchestratorError code
  failureMessage: string;
  priorAttempts: Array<{ attempt: number; error: string }>;
  availableFallbacks: string[];
  stepInputSummary: string;
}

// The RECOVERY agent runs a cheap rule-based pass first. It only asks Claude
// when the heuristics don't yield a clear decision.
export class RecoveryAgent {
  constructor(
    private readonly client: ClaudeClient,
    private readonly opts: { model: string },
  ) {}

  async decide(input: RecoveryInput): Promise<RecoveryOutput> {
    const fast = this.fastDecide(input);
    if (fast) return fast;
    const { data } = await this.client.callJson<RecoveryOutput>(this.opts.model, recoveryPrompt({
      failedStepDescription: input.stepDescription,
      failureClass: input.failureClass,
      failureMessage: input.failureMessage,
      priorAttempts: input.priorAttempts,
      availableFallbacks: input.availableFallbacks,
      stepInputSummary: input.stepInputSummary,
    }));
    return {
      decision: data.decision ?? "escalate",
      alternateConnector: data.alternateConnector ?? null,
      reason: data.reason ?? "",
      confidence: data.confidence ?? 0.6,
      patchHint: data.patchHint ?? null,
    };
  }

  private fastDecide(input: RecoveryInput): RecoveryOutput | null {
    const a = input.priorAttempts.length;
    // Transient network/upstream: retry up to 2x.
    if (
      ["CONNECTOR_RATE_LIMITED", "CONNECTOR_TIMEOUT", "CONNECTOR_UPSTREAM_ERROR"].includes(
        input.failureClass,
      ) &&
      a < 2
    ) {
      return {
        decision: "retry_same",
        alternateConnector: null,
        reason: `transient ${input.failureClass}, attempt ${a + 1}`,
        confidence: 0.9,
        patchHint: null,
      };
    }
    // Unhealthy connector with fallback: switch.
    if (input.failureClass === "CONNECTOR_UNHEALTHY" && input.availableFallbacks.length > 0) {
      return {
        decision: "retry_alt_connector",
        alternateConnector: input.availableFallbacks[0] ?? null,
        reason: "primary connector unhealthy, switching fallback",
        confidence: 0.9,
        patchHint: null,
      };
    }
    // Auth failure: no amount of retries will help.
    if (input.failureClass === "CONNECTOR_AUTH_FAILED") {
      return {
        decision: "escalate",
        alternateConnector: null,
        reason: "auth failure requires human intervention",
        confidence: 1,
        patchHint: null,
      };
    }
    // Budget exceeded: abort.
    if (input.failureClass === "BUDGET_EXCEEDED") {
      return {
        decision: "abort",
        alternateConnector: null,
        reason: "budget exhausted",
        confidence: 1,
        patchHint: null,
      };
    }
    return null;
  }
}
