// Shared magic strings kept in one place so the planner, orchestrator,
// and policy engine all agree on them.

export const EXECUTION_MODES = ["normal", "dry-run", "simulation", "shadow"] as const;
export type ExecutionMode = (typeof EXECUTION_MODES)[number];

export const APPROVAL_GATES = ["never", "high", "all"] as const;
export type ApprovalGate = (typeof APPROVAL_GATES)[number];

export const TRANSPORT_KINDS = ["mcp", "rest", "graphql", "browser", "internal"] as const;
export type TransportKind = (typeof TRANSPORT_KINDS)[number];

export const AGENT_KINDS = [
  "planning",
  "integration",
  "execution",
  "validation",
  "security",
  "recovery",
  "browser",
  "research",
] as const;
export type AgentKind = (typeof AGENT_KINDS)[number];

// Execution preference order (lower index = preferred).
// Used by the integration agent to pick between candidate connectors.
export const TRANSPORT_PREFERENCE: TransportKind[] = [
  "mcp",
  "rest",
  "graphql",
  "internal",
  "browser",
];
