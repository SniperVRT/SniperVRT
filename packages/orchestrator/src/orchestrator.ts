import {
  Task,
  Plan,
  PlanStep,
  Step,
  StepSchema,
  ConnectorRequest,
  ConnectorResponse,
  OrchestratorError,
  asOrchestratorError,
  maxRisk,
  newStepId,
  ApprovalGate,
} from "@snipervrt/shared";
import { hashPayload, redact } from "@snipervrt/connector-core";
import { applyPatch } from "@snipervrt/planner";
import { summarizeMemoryForPrompt } from "@snipervrt/memory";
import { assertTaskTransition, assertStepTransition, isTerminalTask } from "./state-machine.js";
import type { OrchestratorDeps, ExecuteOptions } from "./types.js";

// The control loop. Responsibilities:
// - plan (or resume)
// - iterate over ready steps
// - run security + integration + execution + validation
// - handle approvals + retries + recovery
// - checkpoint after every transition
// - enforce budgets and state invariants
export class Orchestrator {
  // Tracks (taskId, planStepId) pairs whose most-recent approval was granted.
  // Consumed the next time that step runs so we don't re-request approval.
  private readonly preApprovedSteps = new Set<string>();

  constructor(private readonly deps: OrchestratorDeps) {}

  async advance(taskId: string, opts: ExecuteOptions = {}): Promise<Task> {
    const task = await this.mustGet(taskId);
    if (isTerminalTask(task.status)) return task;

    if (task.status === "pending") {
      return await this.startPlanning(task);
    }
    if (task.status === "planning") {
      return await this.finishPlanning(task);
    }
    if (task.status === "ready") {
      await this.setTaskStatus(task, "running");
      return await this.advance(taskId, opts);
    }
    if (task.status === "running") {
      return await this.runNextStep(task, opts);
    }
    if (task.status === "awaiting_approval") {
      return await this.tryResumeAfterApproval(task);
    }
    if (task.status === "needs_replan") {
      return await this.replan(task);
    }
    if (task.status === "paused") return task;
    return task;
  }

  async executeToCompletion(taskId: string, opts: ExecuteOptions = {}): Promise<Task> {
    const maxIterations = (opts.maxSteps ?? 50) * 4;
    let task = await this.mustGet(taskId);
    for (let i = 0; i < maxIterations; i++) {
      if (isTerminalTask(task.status) || task.status === "awaiting_approval" || task.status === "paused") {
        return task;
      }
      task = await this.advance(task.id, opts);
    }
    return task;
  }

  // ---- Planning -----------------------------------------------------------

  private async startPlanning(task: Task): Promise<Task> {
    await this.setTaskStatus(task, "planning");
    return await this.advance(task.id);
  }

  private async finishPlanning(task: Task): Promise<Task> {
    const memoryEntries = await this.deps.memory.search({
      scope: task.tenantId ? "tenant" : "global",
      tenantId: task.tenantId ?? undefined,
      userId: task.userId ?? undefined,
      text: task.goal.objective,
      kinds: ["workflow_template", "connector_quirk", "field_mapping", "recovery_note", "user_preference"],
      limit: 8,
    });
    const memorySummary = summarizeMemoryForPrompt(memoryEntries);
    const plan = await this.deps.planner.plan(task.goal, {
      connectorCatalog: this.deps.registry.describeForPlanner(),
      memorySummary: memorySummary || undefined,
      executionMode: task.mode,
      approvalGate: (task.goal.metadata.approvalGate as ApprovalGate | undefined) ?? "high",
    });
    const overallRisk = maxRisk(plan.overallRisk, ...plan.steps.map((s) => s.riskLevel));
    const updated = await this.deps.tasks.update(task.id, {
      plan,
      riskLevel: overallRisk,
      status: "ready",
      currentStepId: plan.steps[0]?.id ?? null,
    });
    await this.deps.traces.record({
      taskId: task.id,
      stepId: null,
      kind: "plan_generated",
      agent: "planning",
      connector: null,
      summary: `plan with ${plan.steps.length} steps, risk=${overallRisk}`,
      payloadHash: hashPayload(plan),
      status: "ok",
      tokensUsed: 0,
      latencyMs: 0,
      metadata: { confidence: plan.confidence },
    });
    return updated;
  }

  private async replan(task: Task): Promise<Task> {
    if (!task.plan) throw new OrchestratorError("STATE_TRANSITION_INVALID", "replan without plan");
    const completed = (await this.deps.steps.listForTask(task.id))
      .filter((s) => s.status === "succeeded")
      .map((s) => s.planStepId);
    const current = task.currentStepId ?? task.plan.steps[0]!.id;
    const failureSummary = JSON.stringify((await this.lastFailure(task.id))?.lastError ?? "unknown");
    const patch = await this.deps.planner.replan({
      originalPlan: task.plan,
      currentStepId: current,
      failureSummary,
      completedStepIds: completed,
      connectorCatalog: this.deps.registry.describeForPlanner(),
    });
    if (patch.abort) {
      await this.failTask(task, `recovery aborted: ${patch.abortReason ?? patch.reason}`);
      return await this.mustGet(task.id);
    }
    const newPlan = applyPatch(task.plan, patch);
    const next = newPlan.steps.find((s) => !completed.includes(s.id));
    const updated = await this.deps.tasks.update(task.id, {
      plan: newPlan,
      status: "running",
      currentStepId: next?.id ?? null,
    });
    await this.deps.traces.record({
      taskId: task.id,
      stepId: null,
      kind: "plan_patched",
      agent: "recovery",
      connector: null,
      summary: patch.reason,
      payloadHash: hashPayload(patch),
      status: "ok",
      tokensUsed: 0,
      latencyMs: 0,
      metadata: { newStepCount: patch.newSteps.length, replaced: patch.replaceStepIds },
    });
    return updated;
  }

  // ---- Step execution -----------------------------------------------------

  private async runNextStep(task: Task, opts: ExecuteOptions): Promise<Task> {
    const { plan } = task;
    if (!plan) {
      await this.failTask(task, "running task has no plan");
      return await this.mustGet(task.id);
    }
    const existing = await this.deps.steps.listForTask(task.id);
    const completedPlanIds = new Set(
      existing.filter((s) => s.status === "succeeded").map((s) => s.planStepId),
    );
    const nextPlanStep = plan.steps.find(
      (s) =>
        !completedPlanIds.has(s.id) &&
        s.dependsOn.every((d) => completedPlanIds.has(d)),
    );
    if (!nextPlanStep) {
      return await this.succeedTask(task);
    }
    if ((opts.maxSteps ?? Infinity) <= existing.length) {
      await this.failTask(task, "max step budget reached");
      return await this.mustGet(task.id);
    }

    return await this.runStep(task, nextPlanStep);
  }

  private async runStep(task: Task, planStep: PlanStep): Promise<Task> {
    await this.deps.tasks.update(task.id, { currentStepId: planStep.id });

    // Integration: pick connector + payload.
    const sel = await this.deps.integration.select(planStep, task.goal.constraints);

    // Security + policy check. Skipped if the human already approved this step
    // in a prior pass - we consume the pre-approval here so a replay would
    // trigger policy again.
    const preApprovalKey = `${task.id}:${planStep.id}`;
    const preApproved = this.preApprovedSteps.delete(preApprovalKey);
    const security = preApproved
      ? { outcome: "allow" as const, reason: "pre-approved by operator" }
      : await this.deps.security.evaluate({
      taskId: task.id,
      stepId: planStep.id,
      actionType: planStep.actionType,
      service: sel.connector.descriptor.serviceName,
      capability: sel.capability,
      riskLevel: planStep.riskLevel,
      mode: task.mode,
      intent: planStep.description,
      input: sel.payload,
      tenantId: task.tenantId ?? undefined,
      userId: task.userId ?? undefined,
    });
    await this.deps.traces.record({
      taskId: task.id,
      stepId: planStep.id,
      kind: "policy_evaluated",
      agent: "security",
      connector: sel.connector.descriptor.serviceName,
      summary: security.reason,
      payloadHash: null,
      status: security.outcome === "deny" ? "error" : "ok",
      tokensUsed: 0,
      latencyMs: 0,
      metadata: { outcome: security.outcome },
    });
    if (security.outcome === "deny") {
      await this.failTask(task, `policy deny: ${security.reason}`);
      return await this.mustGet(task.id);
    }
    if (security.outcome === "require_approval") {
      await this.deps.tasks.update(task.id, {
        status: "awaiting_approval",
        approvalsRequired: task.approvalsRequired + 1,
      });
      await this.deps.traces.record({
        taskId: task.id,
        stepId: planStep.id,
        kind: "approval_requested",
        agent: "security",
        connector: sel.connector.descriptor.serviceName,
        summary: `approval requested for ${sel.connector.descriptor.serviceName}.${sel.capability}`,
        payloadHash: null,
        status: "warn",
        tokensUsed: 0,
        latencyMs: 0,
        metadata: { approvalId: security.approvalRequest?.id },
      });
      return await this.mustGet(task.id);
    }

    // Materialize a Step row.
    const step = await this.deps.steps.insert(
      StepSchema.parse({
        id: newStepId(),
        taskId: task.id,
        planStepId: planStep.id,
        description: planStep.description,
        assignedAgent: planStep.assignedAgent,
        connector: sel.connector.descriptor.serviceName,
        actionType: planStep.actionType,
        riskLevel: planStep.riskLevel,
        inputPayload: sel.payload,
        outputPayload: null,
        status: "running",
        retries: 0,
        maxRetries: planStep.retryStrategy.maxAttempts,
        lastError: null,
        confidence: planStep.confidence,
        idempotencyKey: planStep.idempotencyKey ?? null,
        startedAt: new Date().toISOString(),
        finishedAt: null,
      }),
    );
    await this.deps.traces.record({
      taskId: task.id,
      stepId: step.id,
      kind: "step_started",
      agent: "execution",
      connector: step.connector,
      summary: `${step.connector}.${sel.capability}`,
      payloadHash: hashPayload(sel.payload),
      status: "ok",
      tokensUsed: 0,
      latencyMs: 0,
      metadata: {},
    });

    const request: ConnectorRequest = {
      capability: sel.capability,
      input: sel.payload,
      idempotencyKey: planStep.idempotencyKey,
      dryRun: task.mode === "dry-run" || task.mode === "simulation",
      taskId: task.id,
      stepId: step.id,
    };

    let response: ConnectorResponse | null = null;
    try {
      response = await this.deps.execution.run({
        connector: sel.connector,
        request,
        retryStrategy: planStep.retryStrategy,
        onRetry: (err, attempt, waitMs) => {
          void this.deps.traces.record({
            taskId: task.id,
            stepId: step.id,
            kind: "step_retried",
            agent: "execution",
            connector: step.connector,
            summary: `retry #${attempt + 1} after ${waitMs}ms`,
            payloadHash: null,
            status: "warn",
            tokensUsed: 0,
            latencyMs: 0,
            metadata: { error: String(err) },
          });
        },
      });
    } catch (e) {
      return await this.handleStepFailure(task, step, planStep, sel.connector.descriptor.serviceName, e);
    }

    if (!response.ok) {
      return await this.handleStepFailure(
        task,
        step,
        planStep,
        sel.connector.descriptor.serviceName,
        new OrchestratorError(
          (response.error?.code as never) ?? "EXECUTION_FAILED",
          response.error?.message ?? "execution failed",
          { retryable: response.error?.retryable ?? false },
        ),
      );
    }

    // Validation before advancing.
    const validation = await this.deps.validation.validate({
      step: planStep,
      output: response.output,
      constraints: task.goal.constraints,
    });
    await this.deps.traces.record({
      taskId: task.id,
      stepId: step.id,
      kind: "validation_performed",
      agent: "validation",
      connector: step.connector,
      summary: validation.reason,
      payloadHash: null,
      status: validation.passed ? "ok" : "warn",
      tokensUsed: 0,
      latencyMs: 0,
      metadata: { confidence: validation.confidence },
    });
    if (!validation.passed) {
      return await this.handleStepFailure(
        task,
        step,
        planStep,
        sel.connector.descriptor.serviceName,
        new OrchestratorError("VALIDATION_REJECTED", validation.reason, {
          details: { issues: validation.issues },
          retryable: !validation.escalate,
        }),
      );
    }

    await this.deps.steps.update(step.id, {
      outputPayload: response.output,
      status: "succeeded",
      finishedAt: new Date().toISOString(),
    });
    await this.deps.traces.record({
      taskId: task.id,
      stepId: step.id,
      kind: "step_succeeded",
      agent: "execution",
      connector: step.connector,
      summary: response.auditSummary || "ok",
      payloadHash: hashPayload(response.output),
      status: "ok",
      tokensUsed: response.meta.tokensUsed,
      latencyMs: response.meta.latencyMs,
      metadata: {},
    });
    return await this.mustGet(task.id);
  }

  private async handleStepFailure(
    task: Task,
    step: Step,
    planStep: PlanStep,
    service: string,
    error: unknown,
  ): Promise<Task> {
    const err = asOrchestratorError(error);
    const priorAttempts: Array<{ attempt: number; error: string }> = [
      { attempt: step.retries, error: err.message },
    ];

    const fallbacks = this.deps.registry
      .select({ candidateServices: planStep.candidateConnectors, actionType: planStep.actionType })
      .filter((r) => r.connector.descriptor.serviceName !== service)
      .slice(0, 3)
      .map((r) => r.connector.descriptor.serviceName);

    const decision = await this.deps.recovery.decide({
      stepDescription: planStep.description,
      failureClass: err.code,
      failureMessage: err.message,
      priorAttempts,
      availableFallbacks: fallbacks,
      stepInputSummary: JSON.stringify(redact(step.inputPayload)).slice(0, 500),
    });
    await this.deps.traces.record({
      taskId: task.id,
      stepId: step.id,
      kind: "recovery_attempted",
      agent: "recovery",
      connector: service,
      summary: `${decision.decision}: ${decision.reason}`,
      payloadHash: null,
      status: "warn",
      tokensUsed: 0,
      latencyMs: 0,
      metadata: { failureClass: err.code },
    });

    await this.deps.steps.update(step.id, {
      status: "failed",
      lastError: err.toJSON(),
      retries: step.retries + 1,
      finishedAt: new Date().toISOString(),
    });

    switch (decision.decision) {
      case "retry_same":
        await this.deps.tasks.update(task.id, { status: "running" });
        return await this.mustGet(task.id);
      case "retry_alt_connector": {
        // Patch the plan with the alt connector preference and replan implicitly.
        if (decision.alternateConnector && task.plan) {
          const patchedPlan = {
            ...task.plan,
            steps: task.plan.steps.map((s) =>
              s.id === planStep.id
                ? { ...s, preferredConnector: decision.alternateConnector ?? s.preferredConnector }
                : s,
            ),
          };
          await this.deps.tasks.update(task.id, { plan: patchedPlan, status: "running" });
        } else {
          await this.deps.tasks.update(task.id, { status: "needs_replan" });
        }
        return await this.mustGet(task.id);
      }
      case "patch_plan":
        await this.deps.tasks.update(task.id, { status: "needs_replan" });
        return await this.mustGet(task.id);
      case "escalate":
        await this.deps.tasks.update(task.id, { status: "awaiting_approval" });
        return await this.mustGet(task.id);
      case "abort":
      default:
        await this.failTask(task, `recovery aborted: ${decision.reason}`);
        return await this.mustGet(task.id);
    }
  }

  // ---- Approvals ---------------------------------------------------------

  async respondToApproval(
    approvalId: string,
    decision: "approved" | "rejected",
    by: string,
    reason?: string,
  ): Promise<Task> {
    const approval = await this.deps.approvals.get(approvalId);
    if (!approval) throw new OrchestratorError("UNKNOWN", "approval not found");
    const updated = await this.deps.approvals.respond(approvalId, decision, by, reason);
    const task = await this.mustGet(approval.taskId);
    await this.deps.traces.record({
      taskId: task.id,
      stepId: approval.stepId,
      kind: "approval_resolved",
      agent: "security",
      connector: approval.connector,
      summary: `${decision}${reason ? ": " + reason : ""}`,
      payloadHash: null,
      status: decision === "approved" ? "ok" : "warn",
      tokensUsed: 0,
      latencyMs: 0,
      metadata: { approvalId, by },
    });
    if (decision === "approved") {
      this.preApprovedSteps.add(`${task.id}:${approval.stepId}`);
      await this.deps.tasks.update(task.id, {
        status: "running",
        approvalsReceived: task.approvalsReceived + 1,
      });
    } else {
      await this.failTask(task, `approval rejected: ${reason ?? "no reason"}`);
    }
    void updated;
    return await this.mustGet(task.id);
  }

  private async tryResumeAfterApproval(task: Task): Promise<Task> {
    const pending = await this.deps.approvals.listPending(task.id);
    if (pending.length === 0) {
      await this.setTaskStatus(task, "running");
      return await this.mustGet(task.id);
    }
    return task;
  }

  // ---- Terminal transitions ----------------------------------------------

  private async succeedTask(task: Task): Promise<Task> {
    const steps = await this.deps.steps.listForTask(task.id);
    const artifacts: Record<string, unknown> = {};
    for (const s of steps) {
      if (s.status === "succeeded" && s.outputPayload) artifacts[s.planStepId] = s.outputPayload;
    }
    const updated = await this.deps.tasks.update(task.id, {
      status: "succeeded",
      result: {
        summary: `completed with ${steps.length} steps`,
        artifacts,
        errors: [],
      },
    });
    await this.deps.traces.record({
      taskId: task.id,
      stepId: null,
      kind: "task_succeeded",
      agent: null,
      connector: null,
      summary: updated.result?.summary ?? "",
      payloadHash: null,
      status: "ok",
      tokensUsed: 0,
      latencyMs: 0,
      metadata: {},
    });
    return updated;
  }

  private async failTask(task: Task, reason: string): Promise<void> {
    await this.deps.tasks.update(task.id, {
      status: "failed",
      result: { summary: reason, artifacts: {}, errors: [reason] },
    });
    await this.deps.traces.record({
      taskId: task.id,
      stepId: null,
      kind: "task_failed",
      agent: null,
      connector: null,
      summary: reason,
      payloadHash: null,
      status: "error",
      tokensUsed: 0,
      latencyMs: 0,
      metadata: {},
    });
  }

  // ---- Helpers -----------------------------------------------------------

  private async mustGet(id: string): Promise<Task> {
    const t = await this.deps.tasks.get(id);
    if (!t) throw new OrchestratorError("UNKNOWN", `task ${id} not found`);
    return t;
  }

  private async setTaskStatus(task: Task, next: Task["status"]): Promise<Task> {
    assertTaskTransition(task.status, next);
    return this.deps.tasks.update(task.id, { status: next });
  }

  private async lastFailure(taskId: string) {
    const steps = await this.deps.steps.listForTask(taskId);
    return [...steps].reverse().find((s) => s.status === "failed");
  }
}

void assertStepTransition;
