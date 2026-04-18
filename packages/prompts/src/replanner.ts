import type { Plan } from "@snipervrt/shared";
import { JSON_ONLY_FOOTER, PROMPT_VERSIONS, cap, section, type PromptBundle } from "./shared.js";

export interface ReplannerPromptInput {
  originalPlan: Plan;
  currentStepId: string;
  failureSummary: string;
  completedStepIds: string[];
  connectorCatalog: string;
  memorySummary?: string;
}

const SYSTEM = [
  "You are the REPLANNER. A plan has stalled. Produce a minimal plan PATCH - not a full replan.",
  "",
  "RULES:",
  "- Keep succeeded steps. Never re-run them.",
  "- Replace or insert as few steps as possible.",
  "- If the failure is unrecoverable with available connectors, set abort=true and give a clear reason.",
  "- Prefer a safer fallback connector over retry when the prior connector is clearly broken.",
  "- Keep schemas identical to the original step where possible (reuse input shapes).",
].join("\n");

export function replannerPrompt(input: ReplannerPromptInput): PromptBundle {
  const user = [
    section("Original plan (compact)", cap(JSON.stringify(compactPlan(input.originalPlan)), 4000)),
    section("Completed step ids", input.completedStepIds.join(", ")),
    section("Current step id", input.currentStepId),
    section("Failure summary", input.failureSummary),
    section("Connector catalog", cap(input.connectorCatalog, 4000)),
    section("Relevant memory", input.memorySummary ? cap(input.memorySummary, 2000) : undefined),
    "",
    "Output schema:",
    PATCH_SCHEMA_HINT,
    "",
    JSON_ONLY_FOOTER,
  ]
    .filter(Boolean)
    .join("\n");
  return { version: PROMPT_VERSIONS.replanner, system: SYSTEM, user };
}

function compactPlan(p: Plan) {
  return {
    objective: p.objective,
    summary: p.summary,
    steps: p.steps.map((s) => ({
      id: s.id,
      description: s.description,
      connector: s.preferredConnector,
      actionType: s.actionType,
      riskLevel: s.riskLevel,
      successCondition: s.successCondition,
      dependsOn: s.dependsOn,
    })),
  };
}

export const PATCH_SCHEMA_HINT = `
{
  "reason": "string",
  "replaceStepIds": ["s2"],
  "insertAfter": "s1",
  "newSteps": [ /* same shape as planner steps */ ],
  "markCompletedStepIds": [],
  "abort": false,
  "abortReason": null
}`;
