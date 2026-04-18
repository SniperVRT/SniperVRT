import { request } from "undici";
import {
  CapabilityDescriptor,
  ConnectorRequest,
  OrchestratorError,
  newConnectorId,
} from "@snipervrt/shared";
import { BaseConnector } from "@snipervrt/connector-core";

// Supabase PostgREST connector. Hits the /rest/v1 endpoint with the service
// role key. We deliberately scope this to simple row-level reads + inserts
// rather than arbitrary SQL - high-blast-radius SQL needs its own connector
// with stronger approvals.

export interface SupabaseConfig {
  url: string;
  serviceRoleKey: string;
}

const CAPABILITIES: CapabilityDescriptor[] = [
  {
    name: "select_rows",
    description: "Select rows from a table with optional filter / limit / order.",
    actionType: "read",
    inputSchema: { table: "string", select: "string", filter: "object", limit: "number", order: "string" },
    outputSchema: { items: "array" },
    riskLevel: "low",
    sideEffects: false,
    idempotent: true,
    cost: { approxTokens: 0, approxUsd: 0, approxLatencyMs: 400 },
    examples: [{ table: "tasks", select: "*", limit: 10 }],
  },
  {
    name: "insert_row",
    description: "Insert a single row into a table.",
    actionType: "create",
    inputSchema: { table: "string", row: "object" },
    outputSchema: { items: "array" },
    riskLevel: "medium",
    sideEffects: true,
    idempotent: false,
    cost: { approxTokens: 0, approxUsd: 0, approxLatencyMs: 500 },
    examples: [],
  },
  {
    name: "update_rows",
    description: "Update rows matching a filter. Refuses if filter is empty.",
    actionType: "update",
    inputSchema: { table: "string", filter: "object", patch: "object" },
    outputSchema: { items: "array" },
    riskLevel: "high",
    sideEffects: true,
    idempotent: true,
    cost: { approxTokens: 0, approxUsd: 0, approxLatencyMs: 500 },
    examples: [],
  },
  {
    name: "delete_rows",
    description: "Delete rows matching a filter. Refuses if filter is empty.",
    actionType: "delete",
    inputSchema: { table: "string", filter: "object" },
    outputSchema: { items: "array" },
    riskLevel: "critical",
    sideEffects: true,
    idempotent: true,
    cost: { approxTokens: 0, approxUsd: 0, approxLatencyMs: 500 },
    examples: [],
  },
];

export class SupabaseConnector extends BaseConnector {
  constructor(private readonly cfg: SupabaseConfig) {
    super({
      id: newConnectorId(),
      serviceName: "supabase",
      version: "1.0.0",
      transport: "rest",
      authKind: "api_key",
      capabilities: CAPABILITIES,
      riskLevel: "high",
      enabled: true,
      health: "unknown",
      tags: ["database", "supabase"],
      description: "Supabase PostgREST connector (row-level).",
    });
  }

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    return {
      apikey: this.cfg.serviceRoleKey,
      authorization: `Bearer ${this.cfg.serviceRoleKey}`,
      "content-type": "application/json",
      prefer: "return=representation",
      ...extra,
    };
  }

  protected async invoke(cap: CapabilityDescriptor, req: ConnectorRequest): Promise<unknown> {
    const table = String(req.input.table);
    const base = `${this.cfg.url.replace(/\/$/, "")}/rest/v1/${encodeURIComponent(table)}`;
    switch (cap.name) {
      case "select_rows": {
        const select = encodeURIComponent(String(req.input.select ?? "*"));
        const limit = Number(req.input.limit ?? 50);
        const order = req.input.order ? `&order=${encodeURIComponent(String(req.input.order))}` : "";
        const filterQs = filterToQs(req.input.filter as Record<string, unknown> | undefined);
        const url = `${base}?select=${select}&limit=${limit}${order}${filterQs}`;
        const res = await request(url, { headers: this.headers() });
        if (res.statusCode >= 400) throw httpErr(res.statusCode);
        return { items: (await res.body.json()) as unknown[] };
      }
      case "insert_row": {
        const row = req.input.row as Record<string, unknown>;
        const res = await request(base, {
          method: "POST",
          headers: this.headers(),
          body: JSON.stringify(row),
        });
        if (res.statusCode >= 400) throw httpErr(res.statusCode);
        return { items: (await res.body.json()) as unknown[] };
      }
      case "update_rows": {
        const filter = req.input.filter as Record<string, unknown> | undefined;
        if (!filter || Object.keys(filter).length === 0) {
          throw new OrchestratorError("VALIDATION_FAILED", "refusing update without filter");
        }
        const url = `${base}?${filterToQs(filter).replace(/^&/, "")}`;
        const res = await request(url, {
          method: "PATCH",
          headers: this.headers(),
          body: JSON.stringify(req.input.patch),
        });
        if (res.statusCode >= 400) throw httpErr(res.statusCode);
        return { items: (await res.body.json()) as unknown[] };
      }
      case "delete_rows": {
        const filter = req.input.filter as Record<string, unknown> | undefined;
        if (!filter || Object.keys(filter).length === 0) {
          throw new OrchestratorError("VALIDATION_FAILED", "refusing delete without filter");
        }
        const url = `${base}?${filterToQs(filter).replace(/^&/, "")}`;
        const res = await request(url, { method: "DELETE", headers: this.headers() });
        if (res.statusCode >= 400) throw httpErr(res.statusCode);
        return { items: (await res.body.json()) as unknown[] };
      }
      default:
        throw new OrchestratorError("CONNECTOR_NOT_FOUND", `supabase: unknown ${cap.name}`);
    }
  }

  protected override async probe(): Promise<void> {
    const res = await request(`${this.cfg.url.replace(/\/$/, "")}/rest/v1/?select=1`, {
      headers: this.headers(),
    });
    if (res.statusCode >= 400 && res.statusCode !== 404) throw httpErr(res.statusCode);
  }
}

function filterToQs(filter?: Record<string, unknown>): string {
  if (!filter) return "";
  const parts: string[] = [];
  for (const [k, v] of Object.entries(filter)) {
    parts.push(`&${encodeURIComponent(k)}=eq.${encodeURIComponent(String(v))}`);
  }
  return parts.join("");
}

function httpErr(code: number) {
  if (code === 401 || code === 403)
    return new OrchestratorError("CONNECTOR_AUTH_FAILED", `supabase ${code}`);
  if (code === 429)
    return new OrchestratorError("CONNECTOR_RATE_LIMITED", `supabase ${code}`, { retryable: true });
  if (code >= 500)
    return new OrchestratorError("CONNECTOR_UPSTREAM_ERROR", `supabase ${code}`, { retryable: true });
  return new OrchestratorError("CONNECTOR_UPSTREAM_ERROR", `supabase ${code}`);
}
