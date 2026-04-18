import { TaskStatus, StepStatus, OrchestratorError } from "@snipervrt/shared";

// Explicit state graph. Any transition not in here throws. Keeping transitions
// as a table, not scattered conditionals, makes the lifecycle inspectable and
// enforceable at runtime.

export const TASK_TRANSITIONS: Record<TaskStatus, TaskStatus[]> = {
  pending: ["planning", "cancelled"],
  planning: ["awaiting_approval", "ready", "failed"],
  awaiting_approval: ["ready", "cancelled", "failed"],
  ready: ["running", "cancelled"],
  running: ["paused", "needs_replan", "succeeded", "failed", "awaiting_approval", "cancelled"],
  paused: ["running", "cancelled"],
  needs_replan: ["planning", "cancelled", "failed"],
  failed: [],
  succeeded: [],
  cancelled: [],
};

export const STEP_TRANSITIONS: Record<StepStatus, StepStatus[]> = {
  pending: ["ready", "skipped", "cancelled"],
  ready: ["running", "skipped", "awaiting_approval", "cancelled"],
  awaiting_approval: ["running", "cancelled", "failed"],
  running: ["awaiting_validation", "succeeded", "failed", "cancelled"],
  awaiting_validation: ["succeeded", "failed"],
  succeeded: [],
  failed: [],
  skipped: [],
  cancelled: [],
};

export function assertTaskTransition(from: TaskStatus, to: TaskStatus): void {
  if (from === to) return;
  const allowed = TASK_TRANSITIONS[from] ?? [];
  if (!allowed.includes(to)) {
    throw new OrchestratorError(
      "STATE_TRANSITION_INVALID",
      `illegal task transition ${from} -> ${to}`,
    );
  }
}

export function assertStepTransition(from: StepStatus, to: StepStatus): void {
  if (from === to) return;
  const allowed = STEP_TRANSITIONS[from] ?? [];
  if (!allowed.includes(to)) {
    throw new OrchestratorError(
      "STATE_TRANSITION_INVALID",
      `illegal step transition ${from} -> ${to}`,
    );
  }
}

// Convenience: terminal? used by the worker to know when to stop polling.
export function isTerminalTask(s: TaskStatus): boolean {
  return s === "succeeded" || s === "failed" || s === "cancelled";
}
export function isTerminalStep(s: StepStatus): boolean {
  return s === "succeeded" || s === "failed" || s === "skipped" || s === "cancelled";
}
