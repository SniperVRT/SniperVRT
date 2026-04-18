// A deliberate error taxonomy. Recovery and policy logic branch on these codes,
// so we never want a bare Error to leak across a module boundary.

export type ErrorCode =
  | "VALIDATION_FAILED"
  | "POLICY_DENIED"
  | "APPROVAL_REQUIRED"
  | "APPROVAL_REJECTED"
  | "CONNECTOR_NOT_FOUND"
  | "CONNECTOR_UNHEALTHY"
  | "CONNECTOR_AUTH_FAILED"
  | "CONNECTOR_RATE_LIMITED"
  | "CONNECTOR_TIMEOUT"
  | "CONNECTOR_UPSTREAM_ERROR"
  | "PLANNER_PARSE_FAILED"
  | "PLANNER_UNSUPPORTED"
  | "EXECUTION_FAILED"
  | "VALIDATION_REJECTED"
  | "RECOVERY_EXHAUSTED"
  | "BUDGET_EXCEEDED"
  | "IDEMPOTENCY_CONFLICT"
  | "STATE_TRANSITION_INVALID"
  | "MEMORY_UNAVAILABLE"
  | "BROWSER_STATE_UNAVAILABLE"
  | "UNKNOWN";

export interface OrchestratorErrorDetails {
  [key: string]: unknown;
}

export class OrchestratorError extends Error {
  public readonly code: ErrorCode;
  public readonly details: OrchestratorErrorDetails;
  public readonly retryable: boolean;
  public readonly approvalRequired: boolean;

  constructor(
    code: ErrorCode,
    message: string,
    opts: {
      details?: OrchestratorErrorDetails;
      retryable?: boolean;
      approvalRequired?: boolean;
      cause?: unknown;
    } = {},
  ) {
    super(message);
    this.name = "OrchestratorError";
    this.code = code;
    this.details = opts.details ?? {};
    this.retryable = opts.retryable ?? defaultRetryable(code);
    this.approvalRequired = opts.approvalRequired ?? code === "APPROVAL_REQUIRED";
    if (opts.cause) (this as { cause?: unknown }).cause = opts.cause;
  }

  toJSON() {
    return {
      name: this.name,
      code: this.code,
      message: this.message,
      details: this.details,
      retryable: this.retryable,
      approvalRequired: this.approvalRequired,
    };
  }
}

function defaultRetryable(code: ErrorCode): boolean {
  switch (code) {
    case "CONNECTOR_RATE_LIMITED":
    case "CONNECTOR_TIMEOUT":
    case "CONNECTOR_UPSTREAM_ERROR":
    case "PLANNER_PARSE_FAILED":
    case "EXECUTION_FAILED":
      return true;
    default:
      return false;
  }
}

export function isOrchestratorError(e: unknown): e is OrchestratorError {
  return e instanceof OrchestratorError;
}

export function asOrchestratorError(e: unknown): OrchestratorError {
  if (isOrchestratorError(e)) return e;
  if (e instanceof Error) {
    return new OrchestratorError("UNKNOWN", e.message, { cause: e });
  }
  return new OrchestratorError("UNKNOWN", String(e));
}
