# Connectors

A connector is any class that implements the `Connector` interface from `@snipervrt/connector-core`. Every connector must:

1. Expose a **`ConnectorDescriptor`** — id, service name, transport, auth kind, capabilities, tags.
2. Honour **dry-run mode** (never call the network when `request.dryRun` is true).
3. Declare **risk level** per capability (the policy engine + security agent reason about it).
4. Map vendor errors to **`OrchestratorError`** codes (`CONNECTOR_AUTH_FAILED`, `CONNECTOR_RATE_LIMITED`, `CONNECTOR_UPSTREAM_ERROR`, …).

`BaseConnector` gives you most of this for free: idempotency ledger, dry-run short-circuit, latency timing, uniform error wrapping, audit summary.

## Shipped connectors

| Service    | Package                           | Transport | Notable                                  |
|------------|-----------------------------------|-----------|------------------------------------------|
| Gmail      | `@snipervrt/connector-gmail`      | REST      | OAuth2 refresh flow cached with 60s buffer |
| GCal       | `@snipervrt/connector-gcal`       | REST      | Reuses `GoogleAuth` from the Gmail package |
| Notion     | `@snipervrt/connector-notion`     | REST      | API version `2022-06-28`                 |
| GitHub     | `@snipervrt/connector-github`     | REST      | 403/429 → `CONNECTOR_RATE_LIMITED`       |
| Supabase   | `@snipervrt/connector-supabase`   | REST      | Refuses unfiltered update/delete         |
| MCP        | `@snipervrt/connector-mcp`        | MCP       | Wraps any MCP server as a first-class connector; risk inferred from tool name |
| Browser    | `@snipervrt/connector-browser`    | Browser   | Playwright digest-based automation, with host allowlist |

## Transport preference

The registry scores candidates using `TRANSPORT_PREFERENCE` from `@snipervrt/shared`:

```
mcp  ▶  rest  ▶  graphql  ▶  internal  ▶  browser
```

Each position adds a bonus to the candidate's selection score (lower index = larger bonus). A preferred-service hint adds +40, an explicit candidate list adds +15, healthy state adds +10, unhealthy removes the candidate entirely.

This is why browser automation is never the default: the only way it wins is when nothing else supports the requested action.

## Risk classification

Each capability declares its own `riskLevel`. Side-effecting actions (`send`, `delete`, `submit`, `share`) are `high`; `create`, `update`, `upload`, `configure`, `export` are `medium`; `read`, `search`, `list`, `summarize` are `low`. Connectors can override this per-request by overriding `classifyRisk()` (e.g. "delete 1 row" vs "delete all rows" should escalate).

## Health checks

`healthCheck()` should be cheap. `BaseConnector.probe()` is the extension point — override it to hit a lightweight endpoint. The registry's `checkHealthAll()` runs them in parallel and updates `descriptor.health`, which feeds back into scoring.

## MCP bridge

`buildMcpConnector()` introspects an MCP server's tool list and synthesizes a `ConnectorDescriptor` on the fly. It infers `actionType` from the tool name (verbs like `create_*`, `delete_*`, `send_*`, …) and risk level from the action. Pass `riskOverrides` if the server exposes a side-effecting tool that looks read-only, or vice versa.

## Adding your own

See [`adding-a-connector.md`](adding-a-connector.md) for a minimum viable example.
