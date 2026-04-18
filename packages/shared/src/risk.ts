// Risk classification: used by planner, policy engine, orchestrator.
// Explicit levels keep policy rules declarative rather than string-matched.

import { z } from "zod";

export const RiskLevel = z.enum(["low", "medium", "high", "critical"]);
export type RiskLevel = z.infer<typeof RiskLevel>;

export const RISK_ORDER: Record<RiskLevel, number> = {
  low: 0,
  medium: 1,
  high: 2,
  critical: 3,
};

export function atLeast(a: RiskLevel, b: RiskLevel): boolean {
  return RISK_ORDER[a] >= RISK_ORDER[b];
}

export function maxRisk(...levels: RiskLevel[]): RiskLevel {
  return levels.reduce<RiskLevel>(
    (acc, cur) => (RISK_ORDER[cur] > RISK_ORDER[acc] ? cur : acc),
    "low",
  );
}

// Canonical action verbs. Planner and connectors use these rather than
// free-form verbs so policy rules can match reliably.
export const ActionType = z.enum([
  "read",
  "search",
  "list",
  "summarize",
  "draft",
  "stage",
  "create",
  "update",
  "delete",
  "send",
  "submit",
  "purchase",
  "upload",
  "download",
  "share",
  "authenticate",
  "configure",
  "export",
]);
export type ActionType = z.infer<typeof ActionType>;

// Default risk mapping. Connectors can override per-action via classifyRisk().
export const DEFAULT_ACTION_RISK: Record<ActionType, RiskLevel> = {
  read: "low",
  search: "low",
  list: "low",
  summarize: "low",
  draft: "low",
  stage: "low",
  create: "medium",
  update: "medium",
  export: "medium",
  download: "medium",
  share: "high",
  send: "high",
  submit: "high",
  upload: "high",
  delete: "high",
  configure: "high",
  authenticate: "critical",
  purchase: "critical",
};
