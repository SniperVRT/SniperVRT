import { z } from "zod";
import { ActionType, RiskLevel } from "../risk.js";
import { AGENT_KINDS } from "../constants.js";

export const StepStatus = z.enum([
  "pending",
  "ready",
  "running",
  "awaiting_approval",
  "awaiting_validation",
  "succeeded",
  "failed",
  "skipped",
  "cancelled",
]);
export type StepStatus = z.infer<typeof StepStatus>;

export const StepSchema = z.object({
  id: z.string(),
  taskId: z.string(),
  planStepId: z.string(),
  description: z.string(),
  assignedAgent: z.enum(AGENT_KINDS),
  connector: z.string().nullable(),
  actionType: ActionType,
  riskLevel: RiskLevel,
  inputPayload: z.record(z.unknown()).default({}),
  outputPayload: z.record(z.unknown()).nullable().default(null),
  status: StepStatus,
  retries: z.number().int().nonnegative().default(0),
  maxRetries: z.number().int().nonnegative().default(2),
  lastError: z.unknown().nullable().default(null),
  confidence: z.number().min(0).max(1).default(0.7),
  idempotencyKey: z.string().nullable().default(null),
  startedAt: z.string().datetime().nullable().default(null),
  finishedAt: z.string().datetime().nullable().default(null),
});
export type Step = z.infer<typeof StepSchema>;
