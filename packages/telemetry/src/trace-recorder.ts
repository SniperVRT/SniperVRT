import type { Trace, TraceKind, AgentKind } from "@snipervrt/shared";
import { newTraceId } from "@snipervrt/shared";

// The trace recorder interface. Backends: in-memory (tests), Postgres (prod).
export interface TraceRecorder {
  record(input: Omit<Trace, "id" | "timestamp">): Promise<Trace>;
  listForTask(taskId: string): Promise<Trace[]>;
}

export class InMemoryTraceRecorder implements TraceRecorder {
  private readonly traces: Trace[] = [];

  async record(input: Omit<Trace, "id" | "timestamp">): Promise<Trace> {
    const trace: Trace = {
      id: newTraceId(),
      timestamp: new Date().toISOString(),
      ...input,
    };
    this.traces.push(trace);
    return trace;
  }
  async listForTask(taskId: string): Promise<Trace[]> {
    return this.traces.filter((t) => t.taskId === taskId);
  }
  all(): Trace[] {
    return [...this.traces];
  }
  clear() {
    this.traces.length = 0;
  }
}

export function traceNote(
  taskId: string,
  kind: TraceKind,
  summary: string,
  extra: Partial<Trace> = {},
): Omit<Trace, "id" | "timestamp"> {
  return {
    taskId,
    stepId: extra.stepId ?? null,
    kind,
    agent: (extra.agent as AgentKind | null) ?? null,
    connector: extra.connector ?? null,
    summary,
    payloadHash: extra.payloadHash ?? null,
    status: extra.status ?? "ok",
    tokensUsed: extra.tokensUsed ?? 0,
    latencyMs: extra.latencyMs ?? 0,
    metadata: extra.metadata ?? {},
  };
}
