import {
  CapabilityDescriptor,
  ConnectorDescriptor,
  ConnectorRequest,
  OrchestratorError,
  newConnectorId,
} from "@snipervrt/shared";
import { BaseConnector } from "@snipervrt/connector-core";

// MCP bridge. Wraps a connected MCP server as a first-class Connector so the
// registry + planner + orchestrator treat it the same as any REST integration.
// We lazy-import the MCP SDK so the rest of the system runs without it if no
// MCP servers are configured.

export interface McpClientLike {
  listTools(): Promise<Array<{ name: string; description?: string; inputSchema?: unknown }>>;
  callTool(name: string, args: Record<string, unknown>): Promise<unknown>;
  close(): Promise<void>;
}

export interface McpConnectorOptions {
  serviceName: string;
  tags?: string[];
  description?: string;
  // Override risk per tool name if you know the server is side-effecting.
  riskOverrides?: Record<string, "low" | "medium" | "high" | "critical">;
  client: McpClientLike;
}

export class McpConnector extends BaseConnector {
  constructor(private readonly opts: McpConnectorOptions, descriptor: ConnectorDescriptor) {
    super(descriptor);
  }

  protected async invoke(cap: CapabilityDescriptor, req: ConnectorRequest): Promise<unknown> {
    try {
      const result = await this.opts.client.callTool(cap.name, req.input);
      return result ?? {};
    } catch (e) {
      throw new OrchestratorError("CONNECTOR_UPSTREAM_ERROR", `mcp ${cap.name} failed`, {
        retryable: true,
        cause: e,
      });
    }
  }

  protected override async probe(): Promise<void> {
    await this.opts.client.listTools();
  }
}

// Builds an McpConnector by probing the server's tool list. The heuristic for
// risk: anything that sounds like a write action (create/update/delete/send/etc.)
// is tagged medium unless overridden.
export async function buildMcpConnector(opts: McpConnectorOptions): Promise<McpConnector> {
  const tools = await opts.client.listTools();
  const capabilities: CapabilityDescriptor[] = tools.map((t) => {
    const action = inferAction(t.name);
    const risk = opts.riskOverrides?.[t.name] ?? inferRisk(action);
    return {
      name: t.name,
      description: t.description ?? t.name,
      actionType: action,
      inputSchema: (t.inputSchema as Record<string, unknown>) ?? {},
      outputSchema: {},
      riskLevel: risk,
      sideEffects: action !== "read" && action !== "search" && action !== "list",
      idempotent: ["read", "search", "list", "summarize"].includes(action),
      cost: { approxTokens: 0, approxUsd: 0, approxLatencyMs: 400 },
      examples: [],
    };
  });
  const descriptor: ConnectorDescriptor = {
    id: newConnectorId(),
    serviceName: opts.serviceName,
    version: "1.0.0",
    transport: "mcp",
    authKind: "custom",
    capabilities,
    riskLevel: "medium",
    enabled: true,
    health: "unknown",
    tags: ["mcp", ...(opts.tags ?? [])],
    description: opts.description ?? `MCP server: ${opts.serviceName}`,
  };
  return new McpConnector(opts, descriptor);
}

function inferAction(name: string): CapabilityDescriptor["actionType"] {
  const n = name.toLowerCase();
  if (/(read|get|fetch)/.test(n)) return "read";
  if (/search|find/.test(n)) return "search";
  if (/list/.test(n)) return "list";
  if (/summari[sz]e/.test(n)) return "summarize";
  if (/draft/.test(n)) return "draft";
  if (/create|new|add/.test(n)) return "create";
  if (/update|patch|modify|edit/.test(n)) return "update";
  if (/delete|remove|destroy/.test(n)) return "delete";
  if (/send|post|message|email/.test(n)) return "send";
  if (/submit/.test(n)) return "submit";
  if (/upload/.test(n)) return "upload";
  if (/download/.test(n)) return "download";
  if (/share/.test(n)) return "share";
  if (/configure|settings/.test(n)) return "configure";
  if (/export/.test(n)) return "export";
  return "read";
}

function inferRisk(action: CapabilityDescriptor["actionType"]) {
  if (["send", "submit", "delete", "share"].includes(action)) return "high" as const;
  if (["create", "update", "configure", "upload", "export"].includes(action))
    return "medium" as const;
  return "low" as const;
}
