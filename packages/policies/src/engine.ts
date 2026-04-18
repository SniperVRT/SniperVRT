import {
  PolicyBundle,
  PolicyDecision,
  PolicyRule,
  ActionType,
  RiskLevel,
  ExecutionMode,
  atLeast,
  OrchestratorError,
} from "@snipervrt/shared";

// Policy evaluator. Declarative + deterministic. First-match-wins ordering.
// Anything the declarative engine can decide does NOT need to call Claude -
// that is the whole point.

export interface PolicyContext {
  actionType: ActionType;
  service: string;
  capability: string;
  riskLevel: RiskLevel;
  tenantId?: string;
  userId?: string;
  mode: ExecutionMode;
}

export class PolicyEngine {
  constructor(private bundle: PolicyBundle) {}

  setBundle(bundle: PolicyBundle) {
    this.bundle = bundle;
  }

  evaluate(ctx: PolicyContext): PolicyDecision {
    for (const rule of this.bundle.rules) {
      if (this.matches(rule, ctx)) {
        return {
          outcome: rule.outcome,
          matchedRuleId: rule.id,
          reason: rule.description,
          approvalScope: rule.approvalScope,
        };
      }
    }
    // No rule matched. Apply approval floor + default outcome.
    if (atLeast(ctx.riskLevel, this.bundle.approvalFloor)) {
      return {
        outcome: "require_approval",
        matchedRuleId: null,
        reason: `risk ${ctx.riskLevel} >= approval floor ${this.bundle.approvalFloor}`,
        approvalScope: "per_action",
      };
    }
    return {
      outcome: this.bundle.defaultOutcome,
      matchedRuleId: null,
      reason: "default outcome",
      approvalScope: "per_action",
    };
  }

  // Assert-style sugar for orchestrator call sites.
  assertAllowed(ctx: PolicyContext): PolicyDecision {
    const d = this.evaluate(ctx);
    if (d.outcome === "deny") {
      throw new OrchestratorError("POLICY_DENIED", d.reason, { details: { ctx } });
    }
    return d;
  }

  private matches(rule: PolicyRule, ctx: PolicyContext): boolean {
    if (rule.actionType && rule.actionType !== ctx.actionType) return false;
    if (rule.service && rule.service !== ctx.service) return false;
    if (rule.capability && rule.capability !== ctx.capability) return false;
    if (rule.tenantId && rule.tenantId !== ctx.tenantId) return false;
    if (rule.userId && rule.userId !== ctx.userId) return false;
    if (rule.mode && rule.mode !== ctx.mode) return false;
    if (rule.minRisk && !atLeast(ctx.riskLevel, rule.minRisk)) return false;
    if (rule.maxRisk && atLeast(ctx.riskLevel, rule.maxRisk) && ctx.riskLevel !== rule.maxRisk) return false;
    return true;
  }
}
