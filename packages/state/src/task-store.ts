import type { Task, TaskStatus, Plan } from "@snipervrt/shared";

export interface TaskStore {
  insert(task: Task): Promise<Task>;
  get(id: string): Promise<Task | null>;
  update(id: string, patch: Partial<Task>): Promise<Task>;
  list(filter?: { status?: TaskStatus; tenantId?: string; limit?: number }): Promise<Task[]>;
  claimNextReady(): Promise<Task | null>;
  setPlan(id: string, plan: Plan): Promise<Task>;
  setStatus(id: string, status: TaskStatus): Promise<Task>;
}
