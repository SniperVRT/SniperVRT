import {
  ConnectorRequest,
  ConnectorResponse,
  RetryStrategy,
  OrchestratorError,
} from "@snipervrt/shared";
import type { Connector } from "@snipervrt/connector-core";
import { withRetry } from "@snipervrt/connector-core";

export interface ExecutionInput {
  connector: Connector;
  request: ConnectorRequest;
  retryStrategy: RetryStrategy;
  onRetry?: (err: unknown, attempt: number, waitMs: number) => void;
}

// The EXECUTION agent is deliberately boring. It does not reason - it invokes
// a connector. All of the 'choose which connector' work already happened in
// the integration agent. This narrow scope keeps tokens off this path.
export class ExecutionAgent {
  async run(input: ExecutionInput): Promise<ConnectorResponse> {
    const validation = await input.connector.validateInput(input.request);
    if (!validation.ok) {
      throw new OrchestratorError("VALIDATION_FAILED", validation.issues.join("; "));
    }
    const normalizedRequest: ConnectorRequest = {
      ...input.request,
      input: validation.normalizedInput ?? input.request.input,
    };
    return withRetry(
      () => input.connector.execute(normalizedRequest),
      input.retryStrategy,
      isRetryableResponse,
      input.onRetry,
    );
  }
}

function isRetryableResponse(result: unknown): boolean {
  if (result instanceof Error) return true;
  const r = result as ConnectorResponse | undefined;
  if (!r) return false;
  return r.error?.retryable === true;
}
