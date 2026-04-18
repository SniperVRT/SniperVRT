# Setup

## Prerequisites

- Node.js 20+
- Docker (optional, for local Postgres + Redis)
- An Anthropic API key (optional — the test suite and the `examples/run-sample-task.ts` demo run without one using the `ScriptedClaudeClient`)

## Install

```bash
npm install
```

This populates the workspaces under `packages/*`, `packages/connectors/*`, `packages/agents/*`, and `apps/*`.

## Environment

Copy the template and fill in what you need:

```bash
cp .env.example .env
```

Core variables:

| Var                         | Purpose                                         |
|-----------------------------|-------------------------------------------------|
| `ANTHROPIC_API_KEY`         | Auth for Claude calls                           |
| `ANTHROPIC_PLANNER_MODEL`   | Default: `claude-opus-4-7`                      |
| `ANTHROPIC_EXECUTOR_MODEL`  | Default: `claude-sonnet-4-6`                    |
| `ANTHROPIC_VALIDATOR_MODEL` | Default: `claude-sonnet-4-6`                    |
| `ANTHROPIC_RECOVERY_MODEL`  | Default: `claude-sonnet-4-6`                    |
| `DATABASE_URL`              | Postgres connection string                      |
| `REDIS_URL`                 | Redis connection (used by optional queueing)    |
| `PER_TASK_TOKEN_BUDGET`     | Hard token cap per task; throws BUDGET_EXCEEDED |
| `DEFAULT_MODE`              | `normal` / `dry-run` / `simulation`             |
| `PORT`                      | API listener port (default 8080)                |

Connector creds (any subset):

| Var                    | Connector           |
|------------------------|---------------------|
| `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` | Gmail + GCal |
| `NOTION_TOKEN`         | Notion              |
| `GITHUB_TOKEN`         | GitHub              |
| `SUPABASE_URL`, `SUPABASE_KEY` | Supabase PostgREST |

## Local Postgres + Redis

```bash
docker compose -f infra/docker-compose.yml up -d
DATABASE_URL=postgres://snipervrt:snipervrt@localhost:5432/snipervrt \
  npm run db:migrate
```

`npm run db:reset` re-applies the schema (destructive — requires `ALLOW_DB_RESET=1`).

## Running

```bash
# Dev mode (tsx, auto-reload)
npm run dev:api
npm run dev:worker

# Production build + run
npm run build
npm run start:api
npm run start:worker
```

The API and worker share the same `boot()` function. For a single-node deployment you can run the worker inline from the API process; for multi-node, point both at Postgres so they see the same queue.

## Tests

```bash
npm test              # full suite (35 tests across 6 files)
npm run test:watch    # vitest watch mode
npm run typecheck     # tsc -b --noEmit across all projects
```

## End-to-end demo (no external services)

```bash
npm run example
```

This uses `InMemory*` stores, a `ScriptedClaudeClient` that returns a deterministic plan, and a fake `DemoNotesConnector`. Useful both as a smoke test and as the canonical wiring reference.
