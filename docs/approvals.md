# Approvals

## Lifecycle

```
Task: running            Security: require_approval   ApprovalStore.create()
                                       │                      │
                                       ▼                      ▼
Task: awaiting_approval  ─────▶  trace(approval_requested)    ApprovalRequest(pending)
                                       │
                                       │  (human responds)
                                       │
                 ┌────────────── approved ──────────────────────┐
                 │                                              │
                 ▼                                              ▼
Task: running     ▲                                  Task: failed
                  │                                  trace(approval_resolved, warn)
runStep re-enters │
(pre-approval bypass     ▲
 consumed once)          │
                        trace(approval_resolved, ok)
```

## Storage

`ApprovalStore` is an interface (in `@snipervrt/policies`) with two implementations:

- `InMemoryApprovalStore` — for tests and single-node runs
- (Postgres-backed one lives under `@snipervrt/state` and hits the `approvals` table)

Each `ApprovalRequest` carries everything a UI needs to show: `title`, `humanSummary`, `effects`, `reversible`, `previewInput` (pre-redacted), `intent`, `riskLevel`, `expiresAt`.

## Redaction

Before a payload is attached to an approval, it passes through `redact()` from `@snipervrt/connector-core`. `SECRET_KEY_PATTERNS` covers `token`, `password`, `api[_-]?key`, `authorization`, etc. The approval UI sees placeholder strings; the connector still executes with the real values.

## TTL

Approvals created with a `ttlMs` will flip to `expired` on `expireOlderThan(now)`. Run this on a cadence from a cron or a Redis-timed job; the in-memory store provides the same method.

## Pre-approval bypass

Once an approval is resolved as `approved`, the orchestrator remembers the `(taskId, stepId)` pair in an in-process set. The next time that step runs, the security check is skipped (with a `policy_evaluated` trace logging `pre-approved by operator`) and the pair is consumed. This prevents the approve-loop-forever bug where each replay re-requests approval for the same step.

For multi-process deployments, surface this as a database column (`approved_step_ids TEXT[]` on `tasks`, or a `consumed` flag on approvals). The in-process set is fine for a single API + worker running from the same `boot()`.

## Explaining with Claude

`SecurityAgent.opts.explainWithLLM = true` will call Claude before creating the approval request to generate:

- a concise `title`
- a plain-language `humanSummary`
- a list of `effects`
- a `reversible: boolean` heuristic

This makes approval requests legible to humans without requiring the UI author to hand-craft language per connector. It runs against the redacted payload, so secrets don't leak.

## HTTP API

```
POST /v1/tasks                    → { task }         intake
POST /v1/tasks/:id/advance        → { task }         run to completion (or hit approval)
GET  /v1/tasks/:id                → { task, steps, traces, pendingApprovals }
POST /v1/approvals/:id/respond    → { task }
  body: { decision: "approved"|"rejected", by: string, reason?: string }
```

See `apps/api/src/index.ts`.
