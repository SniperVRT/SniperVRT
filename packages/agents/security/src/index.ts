import {
  ActionType,
  RiskLevel,
  ExecutionMode,
  ApprovalRequest,
} from "@snipervrt/shared";
import type { ApprovalStore } from "@snipervrt/policies";
import { PolicyEngine } from "@snipervrt/policies";
import { approvalExplanationPrompt, securityPrompt } from "@snipervrt/prompts";
import type { ClaudeClient } from "@snipervrt/planner";
import { redact } from "@snipervrt/connector-core";

export interface SecurityDecision {
  outcome: "allow" | "require_approval" | "deny";
  reason: string;
  approvalRequest?: ApprovalRequest;
}

export interface SecurityAgentContext {
  taskId: string;
  stepId: string;
  actionType: ActionType;
  service: string;
  capability: string;
  riskLevel: RiskLevel;
  mode: ExecutionMode;
  intent: string;
  input: Record<string, unknown>;
  tenantId?: string;
  userId?: string;
}

// The SECURITY agent wraps the PolicyEngine with:
// - approval request creation (via an ApprovalStore)
// - a human-readable summary (optional LLM pass for the explanation only)
// - an optional second-opinion for ambiguous "medium risk" cases
export class SecurityAgent {
  constructor(
    private readonly engine: PolicyEngine,
    private readonly approvals: ApprovalStore,
    private readonly client: ClaudeClient,
    private readonly opts: { model: string; explainWithLLM?: boolean; escalateAmbiguous?: boolean } = { model: "claude-sonnet-4-6" },
  ) {}

  async evaluate(ctx: SecurityAgentContext): Promise<SecurityDecision> {
    const decision = this.engine.evaluate({
      actionType: ctx.actionType,
      service: ctx.service,
      capability: ctx.capability,
      riskLevel: ctx.riskLevel,
      tenantId: ctx.tenantId,
      userId: ctx.userId,
      mode: ctx.mode,
    });

    if (decision.outcome === "deny") {
      return { outcome: "deny", reason: decision.reason };
    }
    if (decision.outcome === "allow") {
      // Ambiguous mid-risk second opinion, optional.
      if (this.opts.escalateAmbiguous && ctx.riskLevel === "medium") {
        const { data } = await this.client.callJson<{ outcome: string; reason: string }>(this.opts.model, securityPrompt({
          actionType: ctx.actionType,
          service: ctx.service,
          capability: ctx.capability,
          riskLevel: ctx.riskLevel,
          intent: ctx.intent,
          redactedInput: redact(ctx.input),
          relevantPolicies: decision.reason,
          mode: ctx.mode,
        }));
        if (data.outcome === "require_approval") {
          return await this.buildApproval(ctx, decision.reason + "; " + data.reason);
        }
        if (data.outcome === "deny") {
          return { outcome: "deny", reason: data.reason };
        }
      }
      return { outcome: "allow", reason: decision.reason };
    }

    // require_approval
    return await this.buildApproval(ctx, decision.reason);
  }

  private async buildApproval(
    ctx: SecurityAgentContext,
    policyReason: string,
  ): Promise<SecurityDecision> {
    let title = `Approve ${ctx.service}.${ctx.capability}`;
    let humanSummary = `${ctx.actionType} via ${ctx.service} with risk=${ctx.riskLevel}.`;
    let effects: string[] = [];
    let reversible = false;

    if (this.opts.explainWithLLM) {
      const { data } = await this.client.callJson<{
        title: string;
        humanSummary: string;
        effects: string[];
        reversible: boolean;
        intent: string;
      }>(this.opts.model, approvalExplanationPrompt({
        actionType: ctx.actionType,
        service: ctx.service,
        capability: ctx.capability,
        riskLevel: ctx.riskLevel,
        redactedInput: redact(ctx.input),
        policyReason,
      }));
      title = data.title ?? title;
      humanSummary = data.humanSummary ?? humanSummary;
      effects = data.effects ?? [];
      reversible = Boolean(data.reversible);
    }

    const approval = await this.approvals.create({
      taskId: ctx.taskId,
      stepId: ctx.stepId,
      title,
      humanSummary,
      intent: ctx.intent,
      actionType: ctx.actionType,
      connector: ctx.service,
      riskLevel: ctx.riskLevel,
      effects,
      reversible,
      previewInput: redact(ctx.input) as Record<string, unknown>,
      ttlMs: 24 * 60 * 60 * 1000,
    });

    return {
      outcome: "require_approval",
      reason: policyReason,
      approvalRequest: approval,
    };
  }
}
