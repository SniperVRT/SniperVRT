import { z } from "zod";
import { EXECUTION_MODES } from "../constants.js";

// What a user hands the orchestrator. The planner turns this into a Plan.
export const GoalSchema = z.object({
  objective: z.string().min(1),
  context: z.string().optional(),
  constraints: z.array(z.string()).default([]),
  preferredServices: z.array(z.string()).default([]),
  forbiddenServices: z.array(z.string()).default([]),
  successCriteria: z.array(z.string()).default([]),
  deadline: z.string().datetime().optional(),
  tenantId: z.string().optional(),
  userId: z.string().optional(),
  mode: z.enum(EXECUTION_MODES).default("normal"),
  budget: z
    .object({
      maxTokens: z.number().int().positive().optional(),
      maxUsd: z.number().positive().optional(),
      maxSteps: z.number().int().positive().optional(),
    })
    .default({}),
  metadata: z.record(z.unknown()).default({}),
});
export type Goal = z.infer<typeof GoalSchema>;
