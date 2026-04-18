# Architecture

The orchestrator is a control loop wrapped around three deterministic primitives and five Claude-backed agents.

```
              ┌───────────────┐
  goal ─────▶│    Intake     │───▶ insert Task(pending) + trace(goal_received)
              └───────────────┘
                      │
                      ▼
              ┌───────────────┐   Planner (Claude) ─ structured JSON only
              │   Planning    │───▶ Plan validated by Zod
              └───────────────┘
                      │
                      ▼
              ┌───────────────┐
              │     Ready     │
              └───────────────┘
                      │
                      ▼
              ┌───────────────────────────────────────────────┐
              │                 Running                       │
              │   for each plan step (deps satisfied):        │
              │     Integration → Security → Execution        │
              │         ↓            ↓           ↓            │
              │   (registry)   (policy+LLM)  (connector)      │
              │                      ↓                        │
              │          require_approval → awaiting_approval │
              │                      ↓                        │
              │                 Validation                    │
              │                      ↓                        │
              │          fail → Recovery → retry/replan/abort │
              └───────────────────────────────────────────────┘
                      │
                      ▼
                 Succeeded / Failed / Cancelled (terminal)
```

## The state machines

`packages/orchestrator/src/state-machine.ts` is the single source of truth.

### Task

```
pending           → planning, cancelled
planning          → awaiting_approval, ready, failed
awaiting_approval → ready, cancelled, failed
ready             → running, cancelled
running           → paused, needs_replan, succeeded, failed,
                    awaiting_approval, cancelled
paused            → running, cancelled
needs_replan      → planning, cancelled, failed
succeeded/failed/cancelled → (terminal, no outgoing edges)
```

### Step

```
pending            → ready, skipped, cancelled
ready              → running, skipped, awaiting_approval, cancelled
awaiting_approval  → running, cancelled, failed
running            → awaiting_validation, succeeded, failed, cancelled
awaiting_validation→ succeeded, failed
succeeded/failed/skipped/cancelled → (terminal)
```

Any transition outside these tables throws `OrchestratorError("STATE_TRANSITION_INVALID")`.

## The agents

Each agent does exactly one thing. All have a fast deterministic path before falling back to Claude, which is the main token-cost lever.

| Agent           | Role                                             | Fast path                                              | LLM path                        |
|-----------------|--------------------------------------------------|--------------------------------------------------------|---------------------------------|
| **Planning**    | goal → structured Plan                           | none                                                   | Claude Opus, retries=1          |
| **Integration** | plan step → (connector, capability, payload)     | registry score gap > 25 → no LLM                       | Claude Sonnet with top-5 menu   |
| **Security**    | policy + approval                                | PolicyEngine first-match → allow/deny direct           | Sonnet for ambiguous medium risk|
| **Execution**   | runs connector with retries                      | always deterministic                                   | never                           |
| **Validation**  | output vs. successCondition                      | regex-matched idioms (items array, ok bool, …)         | Sonnet for deep checks          |
| **Recovery**    | decide retry/replan/escalate/abort               | code-based rules for transient/auth/budget             | Sonnet for plan patches         |
| **Browser**     | Playwright fallback                              | guided mode = deterministic                            | Haiku for adaptive              |

## Dependency graph

```
shared ──▶ connector-core ──▶ connectors/*
   │   ╲         ▲                    ▲
   │    ╲        │                    │
   ▼     ╲       │                    │
prompts   telemetry                   │
   │          │                       │
   ▼          │                       │
planner ──────┤                       │
   │          │                       │
   ▼          │                       │
policies      │                       │
memory        │                       │
state         │                       │
   │          │                       │
   ▼          │                       │
agents/* ─────┴──▶ orchestrator ◀─────┘
                        │
                        ▼
                   apps/api, apps/worker
```

Zero circular references — each package depends only on lower layers.

## The token-cost levers

1. **Deterministic registry scoring** skips the integration LLM call when a single connector is an obvious winner.
2. **Validation fast-path** matches common success-condition idioms with regex.
3. **Recovery fast-path** handles transient errors / auth failures / budget without calling Claude.
4. **Memory summaries** are capped and grouped by kind before being inserted into the planner prompt.
5. **Payload redaction + hashing** keeps audit logs and traces lean and safe.

## Concurrency model

The Postgres task store uses `SELECT ... FOR UPDATE SKIP LOCKED` in `claimNextReady()` so many workers can run in parallel against a single DB without stepping on each other. Idempotency keys flow through `ConnectorRequest`; `BaseConnector` keeps an in-process ledger keyed by `${capability}:${idempotencyKey}`.

## Error taxonomy

`OrchestratorError` with a closed `ErrorCode` set drives every branch. `defaultRetryable()` classifies codes. `asOrchestratorError()` wraps unknown throws. Anything code-based avoids a model round-trip.
