import { z } from "zod";
import { RiskLevel, ActionType } from "../risk.js";

export const ApprovalStatus = z.enum(["pending", "approved", "rejected", "expired"]);
export type ApprovalStatus = z.infer<typeof ApprovalStatus>;

// A human-readable payload designed for UI display.
export const ApprovalRequestSchema = z.object({
  id: z.string(),
  taskId: z.string(),
  stepId: z.string(),
  title: z.string(),
  humanSummary: z.string(),
  actionType: ActionType,
  connector: z.string().nullable(),
  riskLevel: RiskLevel,
  effects: z.array(z.string()).default([]),
  reversible: z.boolean().default(false),
  previewInput: z.record(z.unknown()).default({}),
  // What the step intends to do if approved, in plain language.
  intent: z.string(),
  status: ApprovalStatus.default("pending"),
  requestedAt: z.string().datetime(),
  respondedAt: z.string().datetime().nullable().default(null),
  respondedBy: z.string().nullable().default(null),
  rejectionReason: z.string().nullable().default(null),
  expiresAt: z.string().datetime().nullable().default(null),
});
export type ApprovalRequest = z.infer<typeof ApprovalRequestSchema>;
