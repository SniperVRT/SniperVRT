import {
  TaskSchema,
  GoalSchema,
  Task,
  newTaskId,
  ExecutionMode,
} from "@snipervrt/shared";
import type { OrchestratorDeps, CreateTaskInput } from "./types.js";

// Intake: validate a user goal and insert a pending task row. No Claude calls
// happen here - that keeps intake fast and side-effect free.
export async function intakeTask(
  deps: OrchestratorDeps,
  input: CreateTaskInput,
): Promise<Task> {
  const goal = GoalSchema.parse(input.goal);
  const now = new Date().toISOString();
  const task = TaskSchema.parse({
    id: newTaskId(),
    goal,
    status: "pending",
    priority: input.priority ?? 50,
    riskLevel: "low",
    plan: null,
    currentStepId: null,
    approvalsRequired: 0,
    approvalsReceived: 0,
    result: null,
    mode: (input.mode ?? goal.mode) as ExecutionMode,
    tokensUsed: 0,
    usdSpent: 0,
    attempts: 0,
    tenantId: goal.tenantId ?? null,
    userId: goal.userId ?? null,
    createdAt: now,
    updatedAt: now,
  });
  await deps.tasks.insert(task);
  await deps.traces.record({
    taskId: task.id,
    stepId: null,
    kind: "goal_received",
    agent: null,
    connector: null,
    summary: `received goal: ${task.goal.objective}`,
    payloadHash: null,
    status: "ok",
    tokensUsed: 0,
    latencyMs: 0,
    metadata: { mode: task.mode, priority: task.priority },
  });
  return task;
}
