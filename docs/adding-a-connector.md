# Adding a connector

## 1. Create the package

```
packages/connectors/acme/
  package.json       # "@snipervrt/connector-acme"
  tsconfig.json      # references shared + core
  src/index.ts
```

The `tsconfig.json`:

```json
{
  "extends": "../../../tsconfig.base.json",
  "compilerOptions": { "rootDir": "src", "outDir": "dist" },
  "include": ["src/**/*"],
  "references": [
    { "path": "../../shared" },
    { "path": "../core" }
  ]
}
```

Add the new project reference to the root `tsconfig.json`.

## 2. Write the connector

```ts
import { request } from "undici";
import {
  CapabilityDescriptor,
  ConnectorRequest,
  OrchestratorError,
  newConnectorId,
} from "@snipervrt/shared";
import { BaseConnector } from "@snipervrt/connector-core";

export interface AcmeConfig { token: string; }

const CAPABILITIES: CapabilityDescriptor[] = [
  {
    name: "lookup_widget",
    description: "Fetch a widget by id.",
    actionType: "read",
    inputSchema: { widgetId: "string" },
    outputSchema: { id: "string", name: "string" },
    riskLevel: "low",
    sideEffects: false,
    idempotent: true,
    cost: { approxTokens: 0, approxUsd: 0, approxLatencyMs: 300 },
    examples: [{ widgetId: "w_42" }],
  },
];

export class AcmeConnector extends BaseConnector {
  constructor(private readonly cfg: AcmeConfig) {
    super({
      id: newConnectorId(),
      serviceName: "acme",
      version: "1.0.0",
      transport: "rest",
      authKind: "bearer",
      capabilities: CAPABILITIES,
      riskLevel: "low",
      enabled: true,
      health: "unknown",
      tags: ["widgets"],
      description: "Acme widget API.",
    });
  }

  protected async invoke(cap: CapabilityDescriptor, req: ConnectorRequest): Promise<unknown> {
    switch (cap.name) {
      case "lookup_widget": {
        const res = await request(`https://api.acme.com/widgets/${req.input.widgetId}`, {
          headers: { authorization: `Bearer ${this.cfg.token}` },
        });
        if (res.statusCode >= 400) throw httpErr(res.statusCode);
        return await res.body.json();
      }
      default:
        throw new OrchestratorError("CONNECTOR_NOT_FOUND", `acme: ${cap.name}`);
    }
  }

  protected override async probe(): Promise<void> {
    const res = await request("https://api.acme.com/healthz", {
      headers: { authorization: `Bearer ${this.cfg.token}` },
    });
    if (res.statusCode >= 400) throw httpErr(res.statusCode);
  }
}

function httpErr(code: number) {
  if (code === 401) return new OrchestratorError("CONNECTOR_AUTH_FAILED", `acme ${code}`);
  if (code === 429) return new OrchestratorError("CONNECTOR_RATE_LIMITED", `acme ${code}`, { retryable: true });
  if (code >= 500) return new OrchestratorError("CONNECTOR_UPSTREAM_ERROR", `acme ${code}`, { retryable: true });
  return new OrchestratorError("CONNECTOR_UPSTREAM_ERROR", `acme ${code}`);
}
```

## 3. Register it

In `apps/api/src/index.ts` (or wherever you boot):

```ts
import { AcmeConnector } from "@snipervrt/connector-acme";

registry.register(new AcmeConnector({ token: process.env.ACME_TOKEN! }));
```

That's it — the planner will see it in the catalog, the registry will score it on every step, the policy engine will classify per-capability risk, and the security agent will request approval if any action crosses the approval floor.

## Checklist

- [ ] Capabilities are accurately labeled (`actionType`, `riskLevel`, `sideEffects`, `idempotent`)
- [ ] Error mapping covers `401 / 403 / 404 / 429 / 5xx`
- [ ] `probe()` is cheap and non-destructive
- [ ] Dangerous actions require a filter / scope (see Supabase connector for the "refuse unfiltered update" pattern)
- [ ] Vitest coverage for both the happy path and a rate-limited / auth failure path
- [ ] Added to the root `tsconfig.json` references
