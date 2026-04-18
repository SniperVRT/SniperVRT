import { validatorPrompt } from "@snipervrt/prompts";
import type { ClaudeClient } from "@snipervrt/planner";
import type { PlanStep } from "@snipervrt/shared";

export interface ValidationResult {
  passed: boolean;
  confidence: number;
  reason: string;
  issues: string[];
  escalate: boolean;
  suggestedFix?: string | null;
}

export interface ValidationInput {
  step: PlanStep;
  output: unknown;
  constraints?: string[];
}

// The VALIDATION agent prefers cheap, deterministic checks before calling
// Claude. This keeps validation costs proportional to step risk, not volume.
export class ValidationAgent {
  constructor(
    private readonly client: ClaudeClient,
    private readonly opts: { model: string },
  ) {}

  async validate(input: ValidationInput): Promise<ValidationResult> {
    const fast = this.fastCheck(input);
    if (fast) return fast;

    // Deep check: only when the cheap path is inconclusive.
    const { data } = await this.client.callJson<ValidationResult>(this.opts.model, validatorPrompt({
      stepDescription: input.step.description,
      successCondition: input.step.successCondition,
      expectedOutputSchema: input.step.expectedOutputSchema,
      constraints: input.constraints ?? [],
      rawOutput: input.output,
    }));
    return {
      passed: data.passed,
      confidence: data.confidence ?? 0.7,
      reason: data.reason ?? "",
      issues: data.issues ?? [],
      escalate: data.escalate ?? false,
      suggestedFix: data.suggestedFix ?? null,
    };
  }

  // Deterministic checks. These cover the common success-condition idioms and
  // let us skip a model call for a large fraction of low-risk steps.
  private fastCheck(input: ValidationInput): ValidationResult | null {
    const cond = input.step.successCondition.trim();
    const out = input.output as Record<string, unknown> | undefined;

    // Shapes we recognize directly.
    if (/^output\.items is an array$/.test(cond)) {
      return boolResult(Array.isArray((out as { items?: unknown })?.items), "items array check");
    }
    if (/^output\.summary is a string$/.test(cond)) {
      return boolResult(typeof (out as { summary?: unknown })?.summary === "string", "summary string check");
    }
    if (/^output\.ok is true$/.test(cond)) {
      return boolResult((out as { ok?: unknown })?.ok === true, "ok boolean check");
    }
    return null;
  }
}

function boolResult(passed: boolean, reason: string): ValidationResult {
  return {
    passed,
    confidence: passed ? 1 : 0.9,
    reason,
    issues: passed ? [] : [reason],
    escalate: false,
  };
}
