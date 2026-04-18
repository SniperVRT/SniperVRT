import {
  Goal,
  Plan,
  PlanPatch,
  PlanSchema,
  PlanPatchSchema,
  OrchestratorError,
} from "@snipervrt/shared";
import { plannerPrompt, replannerPrompt } from "@snipervrt/prompts";
import type { ClaudeClient } from "./claude-client.js";

export interface PlannerContext {
  connectorCatalog: string;
  memorySummary?: string;
  priorAttemptsSummary?: string;
  executionMode: string;
  approvalGate: string;
}

export interface PlannerOptions {
  model: string;
  retries?: number;
}

export class Planner {
  constructor(
    private readonly client: ClaudeClient,
    private readonly options: PlannerOptions,
  ) {}

  async plan(goal: Goal, ctx: PlannerContext): Promise<Plan> {
    const bundle = plannerPrompt({ goal, ...ctx });
    const attempts = (this.options.retries ?? 1) + 1;
    let lastError: unknown;
    for (let i = 0; i < attempts; i++) {
      try {
        const { data } = await this.client.callJson<unknown>(this.options.model, bundle, {
          maxTokens: 8192,
        });
        return PlanSchema.parse(data);
      } catch (e) {
        lastError = e;
        if (i === attempts - 1) break;
      }
    }
    throw new OrchestratorError("PLANNER_PARSE_FAILED", "planner exhausted retries", {
      cause: lastError,
    });
  }

  async replan(input: {
    originalPlan: Plan;
    currentStepId: string;
    failureSummary: string;
    completedStepIds: string[];
    connectorCatalog: string;
    memorySummary?: string;
  }): Promise<PlanPatch> {
    const bundle = replannerPrompt(input);
    const { data } = await this.client.callJson<unknown>(this.options.model, bundle, {
      maxTokens: 4096,
    });
    return PlanPatchSchema.parse(data);
  }
}

// Utility: apply a PlanPatch to produce a new Plan.
export function applyPatch(plan: Plan, patch: PlanPatch): Plan {
  if (patch.abort) {
    return plan; // caller is expected to abort the task, not walk the plan
  }
  const surviving = plan.steps.filter((s) => !patch.replaceStepIds.includes(s.id));
  const newSteps = [...patch.newSteps];
  if (patch.insertAfter && newSteps.length > 0) {
    const idx = surviving.findIndex((s) => s.id === patch.insertAfter);
    if (idx >= 0) {
      surviving.splice(idx + 1, 0, ...newSteps);
      return { ...plan, steps: surviving };
    }
  }
  return { ...plan, steps: [...surviving, ...newSteps] };
}
