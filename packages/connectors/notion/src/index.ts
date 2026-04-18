import { request } from "undici";
import {
  CapabilityDescriptor,
  ConnectorRequest,
  OrchestratorError,
  newConnectorId,
} from "@snipervrt/shared";
import { BaseConnector } from "@snipervrt/connector-core";

const BASE = "https://api.notion.com/v1";
const VERSION = "2022-06-28";

export interface NotionConfig {
  token: string;
}

const CAPABILITIES: CapabilityDescriptor[] = [
  {
    name: "search",
    description: "Search pages or databases by title substring.",
    actionType: "search",
    inputSchema: { query: "string", filter: "object" },
    outputSchema: { items: "array" },
    riskLevel: "low",
    sideEffects: false,
    idempotent: true,
    cost: { approxTokens: 0, approxUsd: 0, approxLatencyMs: 700 },
    examples: [{ query: "weekly review" }],
  },
  {
    name: "get_page",
    description: "Fetch a page by id.",
    actionType: "read",
    inputSchema: { pageId: "string" },
    outputSchema: { id: "string", properties: "object" },
    riskLevel: "low",
    sideEffects: false,
    idempotent: true,
    cost: { approxTokens: 0, approxUsd: 0, approxLatencyMs: 500 },
    examples: [],
  },
  {
    name: "create_page",
    description: "Create a new page under a parent.",
    actionType: "create",
    inputSchema: { parent: "object", properties: "object", children: "array" },
    outputSchema: { id: "string", url: "string" },
    riskLevel: "medium",
    sideEffects: true,
    idempotent: false,
    cost: { approxTokens: 0, approxUsd: 0, approxLatencyMs: 900 },
    examples: [],
  },
  {
    name: "update_page",
    description: "Update a page's properties.",
    actionType: "update",
    inputSchema: { pageId: "string", properties: "object" },
    outputSchema: { id: "string" },
    riskLevel: "medium",
    sideEffects: true,
    idempotent: true,
    cost: { approxTokens: 0, approxUsd: 0, approxLatencyMs: 700 },
    examples: [],
  },
];

export class NotionConnector extends BaseConnector {
  constructor(private readonly cfg: NotionConfig) {
    super({
      id: newConnectorId(),
      serviceName: "notion",
      version: "1.0.0",
      transport: "rest",
      authKind: "bearer",
      capabilities: CAPABILITIES,
      riskLevel: "medium",
      enabled: true,
      health: "unknown",
      tags: ["docs", "notion"],
      description: "Notion API connector.",
    });
  }

  protected async invoke(cap: CapabilityDescriptor, req: ConnectorRequest): Promise<unknown> {
    const headers = {
      authorization: `Bearer ${this.cfg.token}`,
      "notion-version": VERSION,
      "content-type": "application/json",
    };
    switch (cap.name) {
      case "search": {
        const res = await request(`${BASE}/search`, {
          method: "POST",
          headers,
          body: JSON.stringify({
            query: req.input.query,
            filter: req.input.filter,
            page_size: req.input.pageSize ?? 10,
          }),
        });
        if (res.statusCode >= 400) throw httpErr(res.statusCode);
        const body = (await res.body.json()) as { results: unknown[] };
        return { items: body.results };
      }
      case "get_page": {
        const id = String(req.input.pageId);
        const res = await request(`${BASE}/pages/${id}`, { headers });
        if (res.statusCode >= 400) throw httpErr(res.statusCode);
        return await res.body.json();
      }
      case "create_page": {
        const res = await request(`${BASE}/pages`, {
          method: "POST",
          headers,
          body: JSON.stringify(req.input),
        });
        if (res.statusCode >= 400) throw httpErr(res.statusCode);
        return await res.body.json();
      }
      case "update_page": {
        const id = String(req.input.pageId);
        const res = await request(`${BASE}/pages/${id}`, {
          method: "PATCH",
          headers,
          body: JSON.stringify({ properties: req.input.properties }),
        });
        if (res.statusCode >= 400) throw httpErr(res.statusCode);
        return await res.body.json();
      }
      default:
        throw new OrchestratorError("CONNECTOR_NOT_FOUND", `notion: unknown ${cap.name}`);
    }
  }

  protected override async probe(): Promise<void> {
    const res = await request(`${BASE}/users/me`, {
      headers: { authorization: `Bearer ${this.cfg.token}`, "notion-version": VERSION },
    });
    if (res.statusCode >= 400) throw httpErr(res.statusCode);
  }
}

function httpErr(code: number) {
  if (code === 401) return new OrchestratorError("CONNECTOR_AUTH_FAILED", `notion ${code}`);
  if (code === 429) return new OrchestratorError("CONNECTOR_RATE_LIMITED", `notion ${code}`, { retryable: true });
  if (code >= 500) return new OrchestratorError("CONNECTOR_UPSTREAM_ERROR", `notion ${code}`, { retryable: true });
  return new OrchestratorError("CONNECTOR_UPSTREAM_ERROR", `notion ${code}`);
}
