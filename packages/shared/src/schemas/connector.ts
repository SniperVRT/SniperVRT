import { z } from "zod";
import { ActionType, RiskLevel } from "../risk.js";
import { TRANSPORT_KINDS } from "../constants.js";

export const AuthKind = z.enum([
  "none",
  "api_key",
  "oauth2",
  "service_account",
  "basic",
  "bearer",
  "custom",
]);
export type AuthKind = z.infer<typeof AuthKind>;

export const ConnectorHealth = z.enum(["healthy", "degraded", "unhealthy", "unknown"]);
export type ConnectorHealth = z.infer<typeof ConnectorHealth>;

// Static description of ONE action the connector exposes. Used by the planner
// to select a connector and by the execution agent to shape payloads.
export const CapabilityDescriptorSchema = z.object({
  name: z.string(),
  description: z.string(),
  actionType: ActionType,
  // JSON Schema (light) for inputs. Keep these small - planner reads them.
  inputSchema: z.record(z.unknown()).default({}),
  outputSchema: z.record(z.unknown()).default({}),
  riskLevel: RiskLevel.default("low"),
  sideEffects: z.boolean().default(false),
  idempotent: z.boolean().default(true),
  cost: z
    .object({
      approxTokens: z.number().int().nonnegative().default(0),
      approxUsd: z.number().nonnegative().default(0),
      approxLatencyMs: z.number().int().nonnegative().default(500),
    })
    .default({}),
  examples: z.array(z.record(z.unknown())).default([]),
});
export type CapabilityDescriptor = z.infer<typeof CapabilityDescriptorSchema>;

export const ConnectorDescriptorSchema = z.object({
  id: z.string(),
  serviceName: z.string(),
  version: z.string().default("1.0.0"),
  transport: z.enum(TRANSPORT_KINDS),
  authKind: AuthKind,
  capabilities: z.array(CapabilityDescriptorSchema),
  riskLevel: RiskLevel.default("low"),
  enabled: z.boolean().default(true),
  health: ConnectorHealth.default("unknown"),
  tags: z.array(z.string()).default([]),
  description: z.string().default(""),
});
export type ConnectorDescriptor = z.infer<typeof ConnectorDescriptorSchema>;

// The one shape everyone uses to ask a connector for something.
export const ConnectorRequestSchema = z.object({
  capability: z.string(),
  input: z.record(z.unknown()).default({}),
  idempotencyKey: z.string().optional(),
  dryRun: z.boolean().default(false),
  taskId: z.string().optional(),
  stepId: z.string().optional(),
});
export type ConnectorRequest = z.infer<typeof ConnectorRequestSchema>;

export const ConnectorResponseSchema = z.object({
  ok: z.boolean(),
  output: z.record(z.unknown()).default({}),
  // Normalized telemetry, always present.
  meta: z
    .object({
      connector: z.string(),
      capability: z.string(),
      latencyMs: z.number().int().nonnegative().default(0),
      retries: z.number().int().nonnegative().default(0),
      tokensUsed: z.number().int().nonnegative().default(0),
      usdSpent: z.number().nonnegative().default(0),
      dryRun: z.boolean().default(false),
    })
    .default({} as never),
  // Human-readable one-liner for the audit trail.
  auditSummary: z.string().default(""),
  // Present on failure.
  error: z
    .object({
      code: z.string(),
      message: z.string(),
      retryable: z.boolean().default(false),
      approvalRequired: z.boolean().default(false),
    })
    .optional(),
});
export type ConnectorResponse = z.infer<typeof ConnectorResponseSchema>;
