# Known limitations and honest edges

A snapshot of things that work, things that don't, and things that need a follow-up before a team would deploy this to prod.

## Working well

- Zero-dependency test run (35 tests, no network, no API key, ~3s)
- Clean monorepo build (`tsc -b`) with composite project references
- Full in-memory end-to-end lifecycle via `examples/run-sample-task.ts`
- Deterministic state machines — illegal transitions throw
- Policy-engine-first security, with an optional LLM second opinion
- Budget accountant short-circuits recovery on `BUDGET_EXCEEDED`
- Dry-run mode never touches the network
- Idempotency ledger per connector, keyed per capability

## Limitations worth flagging

### 1. In-process pre-approval bypass

When a human approves a step, the `(taskId, stepId)` pair is remembered in a `Set` on the Orchestrator instance. This works inside one process. A multi-node deployment needs to persist this — e.g. an `approved_step_ids` column on `tasks`, or a `consumed` boolean on approvals.

### 2. Anthropic SDK is lazy-imported

`AnthropicClaudeClient` does `await import("@anthropic-ai/sdk")` on first call. If you bundle for a constrained runtime, expose `ScriptedClaudeClient` or your own `ClaudeClient` implementation instead.

### 3. Recovery LLM path uses the raw input

When `RecoveryAgent` falls through to Claude, it passes `stepInputSummary` already truncated to 500 chars. For larger inputs you'll want a proper summarization step. For now, increase the slice if your inputs are structured.

### 4. Browser agent adaptive mode is minimal

`BrowserAgent.runAdaptive()` is one-action-per-turn. This is intentional — adaptive browser automation is the most dangerous path in the system. Use guided mode wherever possible and reserve adaptive for goal-driven exploration behind a tight budget.

### 5. Memory search is in-process

`InMemoryMemoryStore.search()` scores candidates by tag overlap + text match + usefulness. There's no vector backend. Swap `MemoryStore` for a pgvector-backed implementation when you need semantic recall.

### 6. Postgres migration is one-shot

`scripts/migrate.ts` runs the canonical DDL (which uses `CREATE ... IF NOT EXISTS`). No versioned migration tool ships with this repo. Use sqitch / goose / atlas when the schema starts evolving.

### 7. No distributed worker queue

Workers poll the Postgres `tasks` table with `FOR UPDATE SKIP LOCKED`. That's enough for dozens of workers. For thousands, layer a Redis stream / RabbitMQ / NATS queue on top and have the worker drain from both.

### 8. Rate limiting is per-connector

Each connector's HTTP errors map to `CONNECTOR_RATE_LIMITED`, and the recovery agent's fast path will `retry_same` up to the retry cap. There's no global rate-limit budget across connectors (yet).

### 9. Web dashboard is a placeholder

`apps/dashboard` exists only as a scaffold. All user interaction happens through `apps/api` today.

### 10. Planner prompt is generic

The shipped `plannerPrompt()` is domain-agnostic. For specialised verticals, wrap or replace it with a prompt that describes your product's constraints — the prompt-versioning convention (`planner.v1.2`) makes this safe.

## Not in scope

- Multi-turn dialogue with end users (this is an agent platform, not a chat UI)
- Fine-grained per-user LLM billing (the `Budget` is task-scoped)
- Running a visual diff / screenshot pipeline inside the browser agent

## If you're evaluating this for production

Before shipping, you probably want to:

1. Wire the approval bypass through the DB (see §1)
2. Add a versioned migration tool (see §6)
3. Write domain prompts + example-rich memory seeds
4. Expand `tests/` with connector-contract tests for each integration you rely on
5. Add OTel export on `@snipervrt/telemetry`
6. Pen-test the browser connector host allowlist + Playwright storage dir
