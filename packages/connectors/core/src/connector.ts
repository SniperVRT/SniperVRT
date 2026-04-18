import type {
  ConnectorDescriptor,
  ConnectorRequest,
  ConnectorResponse,
  CapabilityDescriptor,
  RiskLevel,
} from "@snipervrt/shared";

// The single interface every connector must implement. Keeping it small and
// honest - each method has one clear job.
export interface Connector {
  readonly descriptor: ConnectorDescriptor;

  // Static or cached summary the planner can read cheaply.
  describeCapabilities(): CapabilityDescriptor[];

  // Schema check BEFORE execute. Separating it lets policy + approval flows
  // inspect a request without side effects.
  validateInput(request: ConnectorRequest): Promise<ValidationResult>;

  // Risk classifier. Base implementation uses the capability's declared risk,
  // but connectors can upgrade risk based on payload (e.g. delete all vs one).
  classifyRisk(request: ConnectorRequest): Promise<RiskLevel>;

  // Side-effecting call. MUST honour request.dryRun.
  execute(request: ConnectorRequest): Promise<ConnectorResponse>;

  // Optional post-processor that reshapes vendor output into a stable form.
  // Default: identity. Override if the vendor returns inconsistent shapes.
  normalizeOutput(raw: unknown, capability: string): Promise<Record<string, unknown>>;

  // Cheap liveness probe. Used by the registry's background health loop.
  healthCheck(): Promise<ConnectorHealthReport>;
}

export interface ValidationResult {
  ok: boolean;
  issues: string[];
  // Normalized input that execute() will see. This is where a connector can
  // fill defaults, coerce types, etc.
  normalizedInput?: Record<string, unknown>;
}

export interface ConnectorHealthReport {
  status: "healthy" | "degraded" | "unhealthy" | "unknown";
  latencyMs: number;
  checkedAt: string;
  message?: string;
}
