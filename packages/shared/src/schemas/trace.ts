import { z } from "zod";
import { AGENT_KINDS } from "../constants.js";

// An execution trace record. Durable, audit-grade. One per significant event.
export const TraceKind = z.enum([
  "goal_received",
  "plan_generated",
  "plan_patched",
  "step_dispatched",
  "step_started",
  "step_succeeded",
  "step_failed",
  "step_retried",
  "validation_performed",
  "policy_evaluated",
  "approval_requested",
  "approval_resolved",
  "recovery_attempted",
  "connector_called",
  "connector_health_changed",
  "budget_exceeded",
  "task_succeeded",
  "task_failed",
  "task_cancelled",
  "task_paused",
  "task_resumed",
  "note",
]);
export type TraceKind = z.infer<typeof TraceKind>;

export const TraceSchema = z.object({
  id: z.string(),
  taskId: z.string(),
  stepId: z.string().nullable().default(null),
  kind: TraceKind,
  agent: z.enum(AGENT_KINDS).nullable().default(null),
  connector: z.string().nullable().default(null),
  summary: z.string(),
  // Hashed to avoid logging secrets. Actual payload lives in encrypted storage
  // if retention is enabled.
  payloadHash: z.string().nullable().default(null),
  status: z.enum(["ok", "warn", "error"]).default("ok"),
  tokensUsed: z.number().int().nonnegative().default(0),
  latencyMs: z.number().int().nonnegative().default(0),
  timestamp: z.string().datetime(),
  metadata: z.record(z.unknown()).default({}),
});
export type Trace = z.infer<typeof TraceSchema>;
