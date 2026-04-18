import {
  Task,
  Step,
  TaskStatus,
  StepStatus,
  Plan,
  OrchestratorError,
} from "@snipervrt/shared";
import type { TaskStore } from "./task-store.js";
import type { StepStore } from "./step-store.js";

export class InMemoryTaskStore implements TaskStore {
  private readonly byId = new Map<string, Task>();

  async insert(task: Task) {
    if (this.byId.has(task.id))
      throw new OrchestratorError("STATE_TRANSITION_INVALID", `duplicate task id ${task.id}`);
    this.byId.set(task.id, task);
    return task;
  }
  async get(id: string) {
    return this.byId.get(id) ?? null;
  }
  async update(id: string, patch: Partial<Task>) {
    const prev = this.byId.get(id);
    if (!prev) throw new OrchestratorError("UNKNOWN", `no task ${id}`);
    const updated: Task = { ...prev, ...patch, updatedAt: new Date().toISOString() };
    this.byId.set(id, updated);
    return updated;
  }
  async list(filter?: { status?: TaskStatus; tenantId?: string; limit?: number }) {
    let arr = Array.from(this.byId.values());
    if (filter?.status) arr = arr.filter((t) => t.status === filter.status);
    if (filter?.tenantId) arr = arr.filter((t) => t.tenantId === filter.tenantId);
    arr.sort((a, b) => b.priority - a.priority);
    if (filter?.limit) arr = arr.slice(0, filter.limit);
    return arr;
  }
  async claimNextReady() {
    const candidates = Array.from(this.byId.values())
      .filter((t) => t.status === "ready" || t.status === "running")
      .sort((a, b) => b.priority - a.priority);
    const c = candidates[0];
    if (!c) return null;
    if (c.status === "ready") {
      const updated = { ...c, status: "running" as TaskStatus, updatedAt: new Date().toISOString() };
      this.byId.set(c.id, updated);
      return updated;
    }
    return c;
  }
  async setPlan(id: string, plan: Plan) {
    return this.update(id, { plan });
  }
  async setStatus(id: string, status: TaskStatus) {
    return this.update(id, { status });
  }
}

export class InMemoryStepStore implements StepStore {
  private readonly byId = new Map<string, Step>();

  async insert(step: Step) {
    this.byId.set(step.id, step);
    return step;
  }
  async get(id: string) {
    return this.byId.get(id) ?? null;
  }
  async update(id: string, patch: Partial<Step>) {
    const prev = this.byId.get(id);
    if (!prev) throw new OrchestratorError("UNKNOWN", `no step ${id}`);
    const updated: Step = { ...prev, ...patch };
    this.byId.set(id, updated);
    return updated;
  }
  async listForTask(taskId: string) {
    return Array.from(this.byId.values())
      .filter((s) => s.taskId === taskId)
      .sort((a, b) => (a.startedAt ?? "").localeCompare(b.startedAt ?? ""));
  }
  async setStatus(id: string, status: StepStatus) {
    return this.update(id, { status });
  }
}
