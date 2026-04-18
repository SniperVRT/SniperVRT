import pg from "pg";
import { Task, Step, TaskSchema, StepSchema, TaskStatus, StepStatus, Plan } from "@snipervrt/shared";
import { OrchestratorError } from "@snipervrt/shared";
import type { TaskStore } from "./task-store.js";
import type { StepStore } from "./step-store.js";
import { POSTGRES_SCHEMA } from "./schema.js";

// Thin Postgres-backed stores. We keep the SQL narrow and in one place so the
// in-memory implementation stays a credible test/dev substitute.

export async function migrate(pool: pg.Pool): Promise<void> {
  await pool.query(POSTGRES_SCHEMA);
}

export class PgTaskStore implements TaskStore {
  constructor(private readonly pool: pg.Pool) {}

  async insert(task: Task): Promise<Task> {
    await this.pool.query(
      `INSERT INTO tasks (id, goal, status, priority, risk_level, plan, current_step_id,
        approvals_required, approvals_received, result, mode, tokens_used, usd_spent, attempts,
        tenant_id, user_id, created_at, updated_at)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)`,
      [
        task.id, task.goal, task.status, task.priority, task.riskLevel, task.plan,
        task.currentStepId, task.approvalsRequired, task.approvalsReceived, task.result,
        task.mode, task.tokensUsed, task.usdSpent, task.attempts, task.tenantId,
        task.userId, task.createdAt, task.updatedAt,
      ],
    );
    return task;
  }

  async get(id: string): Promise<Task | null> {
    const r = await this.pool.query("SELECT * FROM tasks WHERE id=$1", [id]);
    const row = r.rows[0];
    return row ? rowToTask(row) : null;
  }

  async update(id: string, patch: Partial<Task>): Promise<Task> {
    const existing = await this.get(id);
    if (!existing) throw new OrchestratorError("UNKNOWN", `task ${id} not found`);
    const updated: Task = { ...existing, ...patch, updatedAt: new Date().toISOString() };
    await this.pool.query(
      `UPDATE tasks SET goal=$2, status=$3, priority=$4, risk_level=$5, plan=$6,
        current_step_id=$7, approvals_required=$8, approvals_received=$9, result=$10,
        mode=$11, tokens_used=$12, usd_spent=$13, attempts=$14, tenant_id=$15, user_id=$16,
        updated_at=$17 WHERE id=$1`,
      [
        id, updated.goal, updated.status, updated.priority, updated.riskLevel, updated.plan,
        updated.currentStepId, updated.approvalsRequired, updated.approvalsReceived, updated.result,
        updated.mode, updated.tokensUsed, updated.usdSpent, updated.attempts, updated.tenantId,
        updated.userId, updated.updatedAt,
      ],
    );
    return updated;
  }

  async list(filter?: { status?: TaskStatus; tenantId?: string; limit?: number }) {
    const where: string[] = [];
    const args: unknown[] = [];
    if (filter?.status) { args.push(filter.status); where.push(`status=$${args.length}`); }
    if (filter?.tenantId) { args.push(filter.tenantId); where.push(`tenant_id=$${args.length}`); }
    const sql = `SELECT * FROM tasks ${where.length ? "WHERE " + where.join(" AND ") : ""}
                 ORDER BY priority DESC, created_at ASC LIMIT ${filter?.limit ?? 100}`;
    const r = await this.pool.query(sql, args);
    return r.rows.map(rowToTask);
  }

  // Uses SELECT ... FOR UPDATE SKIP LOCKED - safe concurrent workers.
  async claimNextReady(): Promise<Task | null> {
    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");
      const r = await client.query(
        `SELECT * FROM tasks WHERE status='ready'
         ORDER BY priority DESC, created_at ASC
         LIMIT 1 FOR UPDATE SKIP LOCKED`,
      );
      const row = r.rows[0];
      if (!row) {
        await client.query("COMMIT");
        return null;
      }
      await client.query("UPDATE tasks SET status='running', updated_at=now() WHERE id=$1", [row.id]);
      await client.query("COMMIT");
      const updated = await this.get(row.id);
      return updated;
    } catch (e) {
      await client.query("ROLLBACK");
      throw e;
    } finally {
      client.release();
    }
  }

  setPlan(id: string, plan: Plan) { return this.update(id, { plan }); }
  setStatus(id: string, status: TaskStatus) { return this.update(id, { status }); }
}

export class PgStepStore implements StepStore {
  constructor(private readonly pool: pg.Pool) {}

  async insert(step: Step) {
    await this.pool.query(
      `INSERT INTO steps (id, task_id, plan_step_id, description, assigned_agent, connector,
        action_type, risk_level, input_payload, output_payload, status, retries, max_retries,
        last_error, confidence, idempotency_key, started_at, finished_at)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)`,
      [
        step.id, step.taskId, step.planStepId, step.description, step.assignedAgent, step.connector,
        step.actionType, step.riskLevel, step.inputPayload, step.outputPayload, step.status,
        step.retries, step.maxRetries, step.lastError, step.confidence, step.idempotencyKey,
        step.startedAt, step.finishedAt,
      ],
    );
    return step;
  }
  async get(id: string) {
    const r = await this.pool.query("SELECT * FROM steps WHERE id=$1", [id]);
    const row = r.rows[0];
    return row ? rowToStep(row) : null;
  }
  async update(id: string, patch: Partial<Step>) {
    const existing = await this.get(id);
    if (!existing) throw new OrchestratorError("UNKNOWN", `step ${id} not found`);
    const updated: Step = { ...existing, ...patch };
    await this.pool.query(
      `UPDATE steps SET description=$2, connector=$3, input_payload=$4, output_payload=$5,
        status=$6, retries=$7, max_retries=$8, last_error=$9, confidence=$10,
        idempotency_key=$11, started_at=$12, finished_at=$13 WHERE id=$1`,
      [
        id, updated.description, updated.connector, updated.inputPayload, updated.outputPayload,
        updated.status, updated.retries, updated.maxRetries, updated.lastError,
        updated.confidence, updated.idempotencyKey, updated.startedAt, updated.finishedAt,
      ],
    );
    return updated;
  }
  async listForTask(taskId: string) {
    const r = await this.pool.query("SELECT * FROM steps WHERE task_id=$1 ORDER BY started_at", [taskId]);
    return r.rows.map(rowToStep);
  }
  setStatus(id: string, status: StepStatus) { return this.update(id, { status }); }
}

function rowToTask(row: Record<string, unknown>): Task {
  return TaskSchema.parse({
    id: row.id,
    goal: row.goal,
    status: row.status,
    priority: row.priority,
    riskLevel: row.risk_level,
    plan: row.plan,
    currentStepId: row.current_step_id,
    approvalsRequired: row.approvals_required,
    approvalsReceived: row.approvals_received,
    result: row.result,
    mode: row.mode,
    tokensUsed: row.tokens_used,
    usdSpent: Number(row.usd_spent),
    attempts: row.attempts,
    tenantId: row.tenant_id,
    userId: row.user_id,
    createdAt: asIso(row.created_at),
    updatedAt: asIso(row.updated_at),
  });
}
function rowToStep(row: Record<string, unknown>): Step {
  return StepSchema.parse({
    id: row.id,
    taskId: row.task_id,
    planStepId: row.plan_step_id,
    description: row.description,
    assignedAgent: row.assigned_agent,
    connector: row.connector,
    actionType: row.action_type,
    riskLevel: row.risk_level,
    inputPayload: row.input_payload,
    outputPayload: row.output_payload,
    status: row.status,
    retries: row.retries,
    maxRetries: row.max_retries,
    lastError: row.last_error,
    confidence: Number(row.confidence),
    idempotencyKey: row.idempotency_key,
    startedAt: row.started_at ? asIso(row.started_at) : null,
    finishedAt: row.finished_at ? asIso(row.finished_at) : null,
  });
}
function asIso(v: unknown): string {
  if (v instanceof Date) return v.toISOString();
  return String(v);
}
