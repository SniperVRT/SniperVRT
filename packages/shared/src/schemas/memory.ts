import { z } from "zod";

// Structured, scoped memory. We never stuff raw history into prompts.
// Retrieval is keyed by scope + tags + text similarity (caller's choice).

export const MemoryKind = z.enum([
  "workflow_template",
  "connector_quirk",
  "field_mapping",
  "user_preference",
  "approval_preference",
  "recovery_note",
  "task_summary",
  "known_failure",
]);
export type MemoryKind = z.infer<typeof MemoryKind>;

export const MemoryScope = z.enum(["global", "tenant", "user", "task"]);
export type MemoryScope = z.infer<typeof MemoryScope>;

export const MemoryEntrySchema = z.object({
  id: z.string(),
  kind: MemoryKind,
  scope: MemoryScope,
  tenantId: z.string().nullable().default(null),
  userId: z.string().nullable().default(null),
  taskId: z.string().nullable().default(null),
  title: z.string(),
  // Short structured body. Memory is designed to be composable into prompts,
  // so keep each entry compact.
  body: z.string(),
  tags: z.array(z.string()).default([]),
  // Optional structured payload for programmatic reuse (not just text).
  data: z.record(z.unknown()).default({}),
  confidence: z.number().min(0).max(1).default(0.7),
  usefulness: z.number().min(0).max(1).default(0.5),
  useCount: z.number().int().nonnegative().default(0),
  createdAt: z.string().datetime(),
  updatedAt: z.string().datetime(),
  expiresAt: z.string().datetime().nullable().default(null),
});
export type MemoryEntry = z.infer<typeof MemoryEntrySchema>;

export const MemoryQuerySchema = z.object({
  scope: MemoryScope.optional(),
  kinds: z.array(MemoryKind).optional(),
  tags: z.array(z.string()).optional(),
  tenantId: z.string().optional(),
  userId: z.string().optional(),
  taskId: z.string().optional(),
  text: z.string().optional(),
  limit: z.number().int().positive().max(50).default(10),
});
export type MemoryQuery = z.infer<typeof MemoryQuerySchema>;
