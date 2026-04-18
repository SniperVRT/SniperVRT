import {
  ConnectorDescriptor,
  ConnectorRequest,
  ConnectorResponse,
  CapabilityDescriptor,
  OrchestratorError,
  RiskLevel,
  asOrchestratorError,
} from "@snipervrt/shared";
import { hashPayload } from "./redaction.js";
import type {
  Connector,
  ConnectorHealthReport,
  ValidationResult,
} from "./connector.js";

// Optional base class. Connectors can implement the interface directly, but
// this removes boilerplate (caching caps, uniform error wrapping, idempotency
// ledger hook, dry-run short-circuit, latency timing, audit summary).
export abstract class BaseConnector implements Connector {
  readonly descriptor: ConnectorDescriptor;
  private readonly idempotencyLedger = new Map<string, ConnectorResponse>();

  constructor(descriptor: ConnectorDescriptor) {
    this.descriptor = descriptor;
  }

  describeCapabilities(): CapabilityDescriptor[] {
    return this.descriptor.capabilities;
  }

  async validateInput(request: ConnectorRequest): Promise<ValidationResult> {
    const cap = this.cap(request.capability);
    if (!cap) {
      return { ok: false, issues: [`unknown capability ${request.capability}`] };
    }
    // Subclasses do schema-specific validation by overriding.
    return { ok: true, issues: [], normalizedInput: request.input };
  }

  async classifyRisk(request: ConnectorRequest): Promise<RiskLevel> {
    const cap = this.cap(request.capability);
    return cap?.riskLevel ?? this.descriptor.riskLevel;
  }

  async execute(request: ConnectorRequest): Promise<ConnectorResponse> {
    const start = Date.now();
    const cap = this.cap(request.capability);
    if (!cap) {
      return this.errorResponse(
        request,
        "CONNECTOR_NOT_FOUND",
        `unknown capability ${request.capability}`,
        start,
      );
    }
    // Idempotency: replay prior response if key + capability match.
    if (request.idempotencyKey) {
      const key = `${request.capability}:${request.idempotencyKey}`;
      const prior = this.idempotencyLedger.get(key);
      if (prior) return prior;
    }
    // Dry-run never reaches the invoke method.
    if (request.dryRun) {
      const preview = await this.describeDryRun(cap, request);
      return {
        ok: true,
        output: preview,
        meta: {
          connector: this.descriptor.serviceName,
          capability: request.capability,
          latencyMs: Date.now() - start,
          retries: 0,
          tokensUsed: 0,
          usdSpent: 0,
          dryRun: true,
        },
        auditSummary: `[dry-run] ${request.capability}`,
      };
    }

    try {
      const raw = await this.invoke(cap, request);
      const output = await this.normalizeOutput(raw, request.capability);
      const response: ConnectorResponse = {
        ok: true,
        output,
        meta: {
          connector: this.descriptor.serviceName,
          capability: request.capability,
          latencyMs: Date.now() - start,
          retries: 0,
          tokensUsed: 0,
          usdSpent: 0,
          dryRun: false,
        },
        auditSummary: await this.summarize(cap, request, output),
      };
      if (request.idempotencyKey) {
        this.idempotencyLedger.set(
          `${request.capability}:${request.idempotencyKey}`,
          response,
        );
      }
      return response;
    } catch (e) {
      const err = asOrchestratorError(e);
      return this.errorResponse(request, err.code, err.message, start, err);
    }
  }

  async normalizeOutput(
    raw: unknown,
    _capability: string,
  ): Promise<Record<string, unknown>> {
    if (raw === null || typeof raw !== "object") return { value: raw };
    if (Array.isArray(raw)) return { items: raw };
    return raw as Record<string, unknown>;
  }

  async healthCheck(): Promise<ConnectorHealthReport> {
    const start = Date.now();
    try {
      await this.probe();
      return {
        status: "healthy",
        latencyMs: Date.now() - start,
        checkedAt: new Date().toISOString(),
      };
    } catch (e) {
      return {
        status: "unhealthy",
        latencyMs: Date.now() - start,
        checkedAt: new Date().toISOString(),
        message: e instanceof Error ? e.message : String(e),
      };
    }
  }

  // ---- Subclass extension points -----------------------------------------

  /** Perform the side-effecting call. Return vendor-native data. */
  protected abstract invoke(
    cap: CapabilityDescriptor,
    request: ConnectorRequest,
  ): Promise<unknown>;

  /** Cheap ping. Default: no-op. */
  protected async probe(): Promise<void> {}

  /** One-line human summary that lands in traces. */
  protected async summarize(
    cap: CapabilityDescriptor,
    request: ConnectorRequest,
    _output: Record<string, unknown>,
  ): Promise<string> {
    return `${this.descriptor.serviceName}.${cap.name}`;
  }

  /** Description of what a real run would do. Override for useful previews. */
  protected async describeDryRun(
    cap: CapabilityDescriptor,
    request: ConnectorRequest,
  ): Promise<Record<string, unknown>> {
    return {
      dryRun: true,
      service: this.descriptor.serviceName,
      capability: cap.name,
      inputHash: hashPayload(request.input),
    };
  }

  // ---- Helpers ------------------------------------------------------------

  protected cap(name: string): CapabilityDescriptor | undefined {
    return this.descriptor.capabilities.find((c) => c.name === name);
  }

  protected errorResponse(
    request: ConnectorRequest,
    code: string,
    message: string,
    startedAt: number,
    cause?: OrchestratorError,
  ): ConnectorResponse {
    return {
      ok: false,
      output: {},
      meta: {
        connector: this.descriptor.serviceName,
        capability: request.capability,
        latencyMs: Date.now() - startedAt,
        retries: 0,
        tokensUsed: 0,
        usdSpent: 0,
        dryRun: request.dryRun,
      },
      auditSummary: `error: ${code}`,
      error: {
        code,
        message,
        retryable: cause?.retryable ?? false,
        approvalRequired: cause?.approvalRequired ?? false,
      },
    };
  }
}

