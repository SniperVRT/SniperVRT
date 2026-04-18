# SniperVRT

A Claude-centered autonomous service orchestration platform. You hand it a goal; it plans, picks connectors, runs with policy + approvals in the loop, validates, retries, and produces auditable artifacts.

The core bet: Claude is the brain, but never the only brain. Declarative layers (state machine, policy engine, connector registry, validation fast-path) decide what they can without an LLM call. Claude only shows up when reasoning is genuinely required.

---

## Highlights

- **Strict JSON boundary with Claude.** The planner, validator, recovery and security agents all call Claude with Zod-validated input/output. No free-form prose survives past the agent boundary.
- **MCP > API > browser.** Connector selection always prefers MCP tools, falls back to direct APIs, and only drops to browser automation when explicitly asked.
- **Policy engine is first-class.** Declarative first-match rules with an approval floor, not a pile of ifs scattered through the orchestrator.
- **Explicit state machines.** `TASK_TRANSITIONS` and `STEP_TRANSITIONS` tables guard every status change — illegal transitions throw.
- **Budget-aware.** A `Budget` accountant runs in front of every Claude call; `BUDGET_EXCEEDED` short-circuits the recovery agent to `abort`.
- **Dry-run / simulation modes** short-circuit connectors before they hit the network.
- **Swappable stores.** In-memory for tests and local dev, Postgres (`FOR UPDATE SKIP LOCKED`) for production.

---

## Quick start

```bash
npm install

# Run the end-to-end example against in-memory backends + a scripted Claude.
# Zero external services or API keys needed.
npm run example

# Run the full test suite.
npm test
```

To run against real infrastructure:

```bash
cp .env.example .env   # fill in keys

docker compose -f infra/docker-compose.yml up -d
DATABASE_URL=postgres://snipervrt:snipervrt@localhost:5432/snipervrt npm run db:migrate

npm run build
npm run start:api    # API on :8080
npm run start:worker # polling worker
```

See [`docs/setup.md`](docs/setup.md) for env details.

---

## Directory map

```
apps/
  api/        # Express HTTP API (intake, advance, approvals, connectors)
  worker/     # Long-running polling worker (same deps as API)
  dashboard/  # placeholder for a UI

packages/
  shared/              # Zod schemas, IDs, errors, risk levels, enums
  connectors/core/     # Connector interface + registry + BaseConnector
  connectors/{gmail,gcal,notion,github,supabase,mcp,browser}
  prompts/             # Every Claude prompt, with versioned IDs
  planner/             # Planner, Claude client (real + scripted), budget
  policies/            # Policy engine, default bundle, approval store
  memory/              # Memory store with tag + text scoring
  state/               # Task/Step stores (in-memory + Postgres) + schema
  telemetry/           # Logger, metrics, trace recorder
  orchestrator/        # State machine, control loop, worker, intake
  agents/{integration,execution,validation,recovery,security,browser,planning}

examples/run-sample-task.ts   # Full in-memory lifecycle demo
tests/                        # Vitest suite (schemas, registry, planner,
                              # policies, orchestrator, state machine)
infra/docker-compose.yml      # Postgres + Redis for local dev
scripts/{migrate,reset-db}.ts
```

---

## Docs

- [`docs/architecture.md`](docs/architecture.md) — control loop, state machines, agent responsibilities
- [`docs/setup.md`](docs/setup.md) — env vars, DB, Docker, running
- [`docs/connectors.md`](docs/connectors.md) — shipped connectors, transport preference
- [`docs/adding-a-connector.md`](docs/adding-a-connector.md) — writing your own
- [`docs/policies.md`](docs/policies.md) — policy engine + approval floors
- [`docs/approvals.md`](docs/approvals.md) — human-in-the-loop lifecycle
- [`docs/limitations.md`](docs/limitations.md) — honest edges and todos

---

## Philosophy

1. **Claude for reasoning. Deterministic code for everything else.** Policy, retries, scoring, validation of obvious shapes — none of these need a model.
2. **Every Claude call has a prompt version.** Prompts are code, not strings. The scripted test client keys on prompt versions so changes break tests, not production.
3. **State is explicit.** The state graph lives in one file. The task schema is one file. The step schema is one file. No hidden implicit states.
4. **Secrets never hit the model.** `redact()` walks every payload before it goes to Claude or to audit logs.
5. **No browser-by-default.** Browser automation is a last-resort capability, not a prime mover. The registry scores MCP/API candidates above it by design.
