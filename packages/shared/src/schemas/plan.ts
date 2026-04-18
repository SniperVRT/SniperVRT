import { z } from "zod";
import { RiskLevel, ActionType } from "../risk.js";
import { AGENT_KINDS } from "../constants.js";

// Strict, parseable planner output. The planner MUST return exactly this shape.
// No free-form prose survives past this boundary.

export const RetryStrategySchema = z.object({
  maxAttempts: z.number().int().min(0).max(10).default(2),
  backoffMs: z.number().int().min(0).default(500),
  backoffMultiplier: z.number().min(1).max(10).default(2),
  escalateOnExhaust: z.boolean().default(true),
});
export type RetryStrategy = z.infer<typeof RetryStrategySchema>;

export const PlanStepSchema = z.object({
  id: z.string().min(1),
  description: z.string().min(1),
  assignedAgent: z.enum(AGENT_KINDS).default("execution"),
  // Preferred connector identifier - logical name, e.g. "gmail".
  preferredConnector: z.string().nullish(),
  candidateConnectors: z.array(z.string()).default([]),
  actionType: ActionType,
  riskLevel: RiskLevel.default("low"),
  dependsOn: z.array(z.string()).default([]),
  // Light JSON Schema-ish hints. Connectors do the hard validation.
  expectedInputSchema: z.record(z.unknown()).optional(),
  expectedOutputSchema: z.record(z.unknown()).optional(),
  inputTemplate: z.record(z.unknown()).default({}),
  successCondition: z.string().min(1),
  requiresApproval: z.boolean().default(false),
  approvalReason: z.string().optional(),
  retryStrategy: RetryStrategySchema.default({}),
  idempotencyKey: z.string().optional(),
  notes: z.string().optional(),
  confidence: z.number().min(0).max(1).default(0.7),
});
export type PlanStep = z.infer<typeof PlanStepSchema>;

export const ApprovalCheckpointSchema = z.object({
  afterStepId: z.string(),
  reason: z.string(),
  riskLevel: RiskLevel,
});
export type ApprovalCheckpoint = z.infer<typeof ApprovalCheckpointSchema>;

export const PlanSchema = z.object({
  version: z.literal(1).default(1),
  objective: z.string().min(1),
  summary: z.string(),
  assumptions: z.array(z.string()).default([]),
  constraints: z.array(z.string()).default([]),
  requiredServices: z.array(z.string()).default([]),
  optionalServices: z.array(z.string()).default([]),
  steps: z.array(PlanStepSchema).min(1),
  dependencies: z.array(z.tuple([z.string(), z.string()])).default([]),
  approvalCheckpoints: z.array(ApprovalCheckpointSchema).default([]),
  successCriteria: z.array(z.string()).default([]),
  stopConditions: z.array(z.string()).default([]),
  fallbackPaths: z
    .array(
      z.object({
        stepId: z.string(),
        alternative: z.string().describe("Plain-text fallback strategy"),
      }),
    )
    .default([]),
  overallRisk: RiskLevel.default("low"),
  confidence: z.number().min(0).max(1).default(0.7),
  estimatedTokenCost: z.number().int().nonnegative().default(0),
});
export type Plan = z.infer<typeof PlanSchema>;

// A compact plan patch emitted by the recovery agent - full replans are
// expensive, so we support surgical patches too.
export const PlanPatchSchema = z.object({
  reason: z.string(),
  replaceStepIds: z.array(z.string()).default([]),
  insertAfter: z.string().optional(),
  newSteps: z.array(PlanStepSchema).default([]),
  markCompletedStepIds: z.array(z.string()).default([]),
  abort: z.boolean().default(false),
  abortReason: z.string().optional(),
});
export type PlanPatch = z.infer<typeof PlanPatchSchema>;
