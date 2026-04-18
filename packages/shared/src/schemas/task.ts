import { z } from "zod";
import { RiskLevel } from "../risk.js";
import { PlanSchema } from "./plan.js";
import { GoalSchema } from "./goal.js";
import { EXECUTION_MODES } from "../constants.js";

// Orchestrator state machine states. Transitions are guarded in
// @snipervrt/orchestrator/state.ts - this enum is the shared source of truth.
export const TaskStatus = z.enum([
  "pending",        // intake received, not planned
  "planning",       // planner running
  "awaiting_approval",
  "ready",          // plan approved, ready to run
  "running",
  "paused",
  "needs_replan",
  "failed",
  "succeeded",
  "cancelled",
]);
export type TaskStatus = z.infer<typeof TaskStatus>;

export const TaskSchema = z.object({
  id: z.string(),
  goal: GoalSchema,
  status: TaskStatus,
  priority: z.number().int().min(0).max(100).default(50),
  riskLevel: RiskLevel.default("low"),
  plan: PlanSchema.nullable(),
  currentStepId: z.string().nullable(),
  approvalsRequired: z.number().int().nonnegative().default(0),
  approvalsReceived: z.number().int().nonnegative().default(0),
  result: z
    .object({
      summary: z.string(),
      artifacts: z.record(z.unknown()).default({}),
      errors: z.array(z.unknown()).default([]),
    })
    .nullable()
    .default(null),
  mode: z.enum(EXECUTION_MODES).default("normal"),
  tokensUsed: z.number().int().nonnegative().default(0),
  usdSpent: z.number().nonnegative().default(0),
  attempts: z.number().int().nonnegative().default(0),
  tenantId: z.string().nullable().default(null),
  userId: z.string().nullable().default(null),
  createdAt: z.string().datetime(),
  updatedAt: z.string().datetime(),
});
export type Task = z.infer<typeof TaskSchema>;
