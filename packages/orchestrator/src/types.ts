import type {
  Goal,
  Task,
  Plan,
  PlanStep,
  ExecutionMode,
  ApprovalGate,
} from "@snipervrt/shared";
import type { TaskStore, StepStore } from "@snipervrt/state";
import type { ApprovalStore, PolicyEngine } from "@snipervrt/policies";
import type { MemoryStore } from "@snipervrt/memory";
import type { ConnectorRegistry } from "@snipervrt/connector-core";
import type { TraceRecorder } from "@snipervrt/telemetry";
import type { Planner, ClaudeClient, Budget } from "@snipervrt/planner";
import type { IntegrationAgent } from "@snipervrt/agent-integration";
import type { ExecutionAgent } from "@snipervrt/agent-execution";
import type { ValidationAgent } from "@snipervrt/agent-validation";
import type { RecoveryAgent } from "@snipervrt/agent-recovery";
import type { SecurityAgent } from "@snipervrt/agent-security";

export interface OrchestratorDeps {
  tasks: TaskStore;
  steps: StepStore;
  approvals: ApprovalStore;
  memory: MemoryStore;
  registry: ConnectorRegistry;
  policy: PolicyEngine;
  traces: TraceRecorder;
  planner: Planner;
  client: ClaudeClient;
  budget?: Budget;
  integration: IntegrationAgent;
  execution: ExecutionAgent;
  validation: ValidationAgent;
  recovery: RecoveryAgent;
  security: SecurityAgent;
}

export interface CreateTaskInput {
  goal: Goal;
  priority?: number;
  mode?: ExecutionMode;
  approvalGate?: ApprovalGate;
}

export interface ExecuteOptions {
  maxSteps?: number;
}

export type { Goal, Task, Plan, PlanStep };
