# Policies

The policy engine is a declarative, first-match-wins evaluator over `PolicyRule`s, with an **approval floor** for anything not explicitly matched.

```ts
const engine = new PolicyEngine(DEFAULT_POLICY_BUNDLE);

const decision = engine.evaluate({
  actionType: "send",
  service: "gmail",
  capability: "send_message",
  riskLevel: "high",
  mode: "normal",
});
// { outcome: "require_approval", matchedRuleId: ..., reason: ..., approvalScope: ... }
```

Outcomes: `allow`, `deny`, `require_approval`.

## Rule matching

A rule matches when every specified predicate holds:

- `actionType` (optional) — exact match
- `service` (optional) — exact match
- `capability` (optional) — exact match
- `tenantId` / `userId` (optional) — exact match
- `mode` (optional) — `normal` / `dry-run` / `simulation`
- `minRisk` (optional) — `ctx.riskLevel >= minRisk`
- `maxRisk` (optional) — `ctx.riskLevel <= maxRisk`

Order matters — the first matching rule wins.

## Default bundle

`DEFAULT_POLICY_BUNDLE` (in `packages/policies/src/defaults.ts`) includes:

1. **Dry-run / simulation → allow.** Nothing side-effecting happens in these modes anyway.
2. **`purchase` / `delete` / `submit` / `send` → require_approval.** Regardless of risk level declared by the connector.
3. **`authenticate` → deny.** You configure auth out-of-band; no Claude-driven flow should touch it.
4. **`configure` → require_approval.**
5. **`read` at low risk → allow.**

The **approval floor** is `high` — anything medium-or-below that doesn't match a rule flows through `defaultOutcome` (allow), and anything at or above the floor triggers `require_approval` even without an explicit rule.

## Writing your own bundle

```ts
import { PolicyEngine, type PolicyBundle } from "@snipervrt/policies";

const bundle: PolicyBundle = {
  version: "1.0.0",
  defaultOutcome: "allow",
  approvalFloor: "medium",
  rules: [
    {
      id: "block-prod-db-writes",
      description: "No writes to prod DB via Claude",
      outcome: "deny",
      service: "supabase",
      tenantId: "tenant_prod",
      minRisk: "medium",
    },
    {
      id: "eng-can-push-github",
      description: "Allow auto PRs from the eng tenant",
      outcome: "allow",
      service: "github",
      actionType: "create",
      tenantId: "tenant_eng",
    },
  ],
};

const engine = new PolicyEngine(bundle);
```

## Why a declarative engine (and not "ask Claude")?

1. **Determinism.** Policy decisions must be reproducible for compliance.
2. **Cost.** A security check on every step would double the token bill.
3. **Auditability.** Rules are diff-able and reviewable. An LLM's opinion isn't.

The security agent still has an **optional LLM second opinion** for ambiguous medium-risk cases (see `SecurityAgent.opts.escalateAmbiguous`). That's for "does this specific payload look sensitive" decisions, not for "is this category of action allowed".

## See also

- `docs/approvals.md` — human-in-the-loop flow when a policy demands approval
- `packages/policies/src/engine.ts` — the evaluator
- `tests/policies/engine.test.ts` — worked examples
