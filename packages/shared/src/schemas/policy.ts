import { z } from "zod";
import { ActionType, RiskLevel } from "../risk.js";
import { EXECUTION_MODES } from "../constants.js";

export const PolicyOutcome = z.enum(["allow", "require_approval", "deny"]);
export type PolicyOutcome = z.infer<typeof PolicyOutcome>;

// A single declarative rule. The policy engine evaluates them in order.
// First match wins. Missing fields are wildcards.
export const PolicyRuleSchema = z.object({
  id: z.string(),
  description: z.string(),
  // Matchers (all ANDed, each optional):
  actionType: ActionType.optional(),
  service: z.string().optional(),
  capability: z.string().optional(),
  minRisk: RiskLevel.optional(),
  maxRisk: RiskLevel.optional(),
  tenantId: z.string().optional(),
  userId: z.string().optional(),
  mode: z.enum(EXECUTION_MODES).optional(),
  // Decision:
  outcome: PolicyOutcome,
  approvalScope: z.enum(["per_action", "per_task", "session"]).default("per_action"),
  allowedRoles: z.array(z.string()).default([]),
  denyReason: z.string().optional(),
});
export type PolicyRule = z.infer<typeof PolicyRuleSchema>;

export const PolicyBundleSchema = z.object({
  version: z.number().int().positive().default(1),
  defaultOutcome: PolicyOutcome.default("allow"),
  rules: z.array(PolicyRuleSchema),
  // Whether to also auto-require approval for anything at or above this risk,
  // even if no explicit rule matched.
  approvalFloor: RiskLevel.default("high"),
});
export type PolicyBundle = z.infer<typeof PolicyBundleSchema>;

// Policy decision returned for a single proposed action.
export const PolicyDecisionSchema = z.object({
  outcome: PolicyOutcome,
  matchedRuleId: z.string().nullable(),
  reason: z.string(),
  approvalScope: z.enum(["per_action", "per_task", "session"]).default("per_action"),
});
export type PolicyDecision = z.infer<typeof PolicyDecisionSchema>;
