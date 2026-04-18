import type { Goal } from "@snipervrt/shared";
import { JSON_ONLY_FOOTER, PROMPT_VERSIONS, cap, section, type PromptBundle } from "./shared.js";

export interface PlannerPromptInput {
  goal: Goal;
  connectorCatalog: string;       // pre-summarized catalog from ConnectorRegistry
  memorySummary?: string;         // compact retrieval from the memory layer
  priorAttemptsSummary?: string;  // recovery-style notes if this is a redo
  executionMode: string;
  approvalGate: string;
}

const SYSTEM = [
  "You are the PLANNER for an autonomous service orchestration platform.",
  "You convert a user goal into an ordered plan of small, concrete steps.",
  "",
  "YOU MUST:",
  "- Use only connectors declared in the provided catalog.",
  "- Prefer MCP > direct API > browser automation in that order.",
  "- Mark every side-effecting step's risk level honestly.",
  "- Add explicit approval checkpoints before high-risk irreversible actions.",
  "- Make steps narrow enough that the execution agent can run them with only step-local context.",
  "- Specify success conditions that a validator can check without running the step again.",
  "- Suggest fallbacks for every step whose primary connector could plausibly fail.",
  "- Reuse prior successful workflow patterns from memory when applicable.",
  "- Never invent services or capabilities not in the catalog.",
  "",
  "RISK LEVELS: low, medium, high, critical.",
  "ACTION TYPES: read, search, list, summarize, draft, stage, create, update, delete,",
  "              send, submit, purchase, upload, download, share, authenticate, configure, export.",
].join("\n");

export function plannerPrompt(input: PlannerPromptInput): PromptBundle {
  const user = [
    section("Objective", input.goal.objective),
    section("Context", input.goal.context),
    section("Constraints", input.goal.constraints.join("\n")),
    section("Preferred services", input.goal.preferredServices.join(", ")),
    section("Forbidden services", input.goal.forbiddenServices.join(", ")),
    section("Success criteria", input.goal.successCriteria.join("\n")),
    section("Execution mode", input.executionMode),
    section("Approval gate", input.approvalGate),
    section("Connector catalog", cap(input.connectorCatalog, 8000)),
    section("Relevant memory", input.memorySummary ? cap(input.memorySummary, 4000) : undefined),
    section("Prior attempts", input.priorAttemptsSummary ? cap(input.priorAttemptsSummary, 2000) : undefined),
    "",
    "Produce JSON matching this schema (fields and types must match exactly):",
    PLANNER_OUTPUT_SCHEMA_HINT,
    "",
    JSON_ONLY_FOOTER,
  ]
    .filter(Boolean)
    .join("\n");

  return {
    version: PROMPT_VERSIONS.planner,
    system: SYSTEM,
    user,
  };
}

// Inline, human-readable schema hint. The real schema is enforced by zod on
// parse; this just helps Claude hit the target.
export const PLANNER_OUTPUT_SCHEMA_HINT = `
{
  "version": 1,
  "objective": "string",
  "summary": "string",
  "assumptions": ["string"],
  "constraints": ["string"],
  "requiredServices": ["string"],
  "optionalServices": ["string"],
  "steps": [
    {
      "id": "s1",
      "description": "string",
      "assignedAgent": "planning|integration|execution|validation|security|recovery|browser|research",
      "preferredConnector": "string|null",
      "candidateConnectors": ["string"],
      "actionType": "read|search|list|summarize|draft|stage|create|update|delete|send|submit|purchase|upload|download|share|authenticate|configure|export",
      "riskLevel": "low|medium|high|critical",
      "dependsOn": ["s0"],
      "expectedInputSchema": {},
      "expectedOutputSchema": {},
      "inputTemplate": {},
      "successCondition": "string",
      "requiresApproval": false,
      "approvalReason": "string|null",
      "retryStrategy": { "maxAttempts": 2, "backoffMs": 500, "backoffMultiplier": 2, "escalateOnExhaust": true },
      "idempotencyKey": "string|null",
      "notes": "string|null",
      "confidence": 0.8
    }
  ],
  "dependencies": [["s0","s1"]],
  "approvalCheckpoints": [{ "afterStepId": "s1", "reason": "string", "riskLevel": "high" }],
  "successCriteria": ["string"],
  "stopConditions": ["string"],
  "fallbackPaths": [{ "stepId": "s1", "alternative": "string" }],
  "overallRisk": "low|medium|high|critical",
  "confidence": 0.8,
  "estimatedTokenCost": 12000
}`;
