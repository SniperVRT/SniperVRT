import {
  ApprovalRequest,
  ApprovalRequestSchema,
  newApprovalId,
  OrchestratorError,
  RiskLevel,
  ActionType,
} from "@snipervrt/shared";

export interface CreateApprovalInput {
  taskId: string;
  stepId: string;
  title: string;
  humanSummary: string;
  intent: string;
  actionType: ActionType;
  connector: string | null;
  riskLevel: RiskLevel;
  effects?: string[];
  reversible?: boolean;
  previewInput?: Record<string, unknown>;
  ttlMs?: number;
}

export interface ApprovalStore {
  create(input: CreateApprovalInput): Promise<ApprovalRequest>;
  get(id: string): Promise<ApprovalRequest | null>;
  listPending(taskId: string): Promise<ApprovalRequest[]>;
  respond(
    id: string,
    decision: "approved" | "rejected",
    by: string,
    reason?: string,
  ): Promise<ApprovalRequest>;
  expireOlderThan(nowIso: string): Promise<number>;
}

// In-memory reference implementation. Useful for tests and single-node runs.
// For production use the Postgres-backed one in @snipervrt/state.
export class InMemoryApprovalStore implements ApprovalStore {
  private readonly byId = new Map<string, ApprovalRequest>();

  async create(input: CreateApprovalInput): Promise<ApprovalRequest> {
    const now = new Date();
    const req = ApprovalRequestSchema.parse({
      id: newApprovalId(),
      taskId: input.taskId,
      stepId: input.stepId,
      title: input.title,
      humanSummary: input.humanSummary,
      actionType: input.actionType,
      connector: input.connector,
      riskLevel: input.riskLevel,
      effects: input.effects ?? [],
      reversible: input.reversible ?? false,
      previewInput: input.previewInput ?? {},
      intent: input.intent,
      requestedAt: now.toISOString(),
      expiresAt: input.ttlMs
        ? new Date(now.getTime() + input.ttlMs).toISOString()
        : null,
    });
    this.byId.set(req.id, req);
    return req;
  }
  async get(id: string) {
    return this.byId.get(id) ?? null;
  }
  async listPending(taskId: string) {
    return Array.from(this.byId.values()).filter(
      (a) => a.taskId === taskId && a.status === "pending",
    );
  }
  async respond(id: string, decision: "approved" | "rejected", by: string, reason?: string) {
    const existing = this.byId.get(id);
    if (!existing) throw new OrchestratorError("UNKNOWN", `approval ${id} not found`);
    if (existing.status !== "pending") {
      throw new OrchestratorError("STATE_TRANSITION_INVALID", `approval ${id} already ${existing.status}`);
    }
    const updated: ApprovalRequest = {
      ...existing,
      status: decision,
      respondedAt: new Date().toISOString(),
      respondedBy: by,
      rejectionReason: decision === "rejected" ? reason ?? null : null,
    };
    this.byId.set(id, updated);
    return updated;
  }
  async expireOlderThan(nowIso: string) {
    const now = new Date(nowIso).getTime();
    let n = 0;
    for (const [id, a] of this.byId.entries()) {
      if (a.status === "pending" && a.expiresAt && new Date(a.expiresAt).getTime() < now) {
        this.byId.set(id, { ...a, status: "expired" });
        n++;
      }
    }
    return n;
  }
}
