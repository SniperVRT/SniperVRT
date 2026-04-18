import type { Step, StepStatus } from "@snipervrt/shared";

export interface StepStore {
  insert(step: Step): Promise<Step>;
  get(id: string): Promise<Step | null>;
  update(id: string, patch: Partial<Step>): Promise<Step>;
  listForTask(taskId: string): Promise<Step[]>;
  setStatus(id: string, status: StepStatus): Promise<Step>;
}
