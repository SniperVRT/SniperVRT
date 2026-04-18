import { request } from "undici";
import {
  CapabilityDescriptor,
  ConnectorDescriptor,
  ConnectorRequest,
  OrchestratorError,
  newConnectorId,
} from "@snipervrt/shared";
import { BaseConnector } from "@snipervrt/connector-core";
import { GoogleAuth } from "./oauth.js";

const BASE = "https://gmail.googleapis.com/gmail/v1/users/me";

export interface GmailConfig {
  clientId: string;
  clientSecret: string;
  refreshToken: string;
}

const CAPABILITIES: CapabilityDescriptor[] = [
  {
    name: "search_messages",
    description: "Search messages with a Gmail query string.",
    actionType: "search",
    inputSchema: { query: "string", maxResults: "number" },
    outputSchema: { items: "array" },
    riskLevel: "low",
    sideEffects: false,
    idempotent: true,
    cost: { approxTokens: 0, approxUsd: 0, approxLatencyMs: 800 },
    examples: [{ query: "newer_than:1d", maxResults: 10 }],
  },
  {
    name: "get_message",
    description: "Fetch a single message by id.",
    actionType: "read",
    inputSchema: { id: "string" },
    outputSchema: { subject: "string", from: "string", snippet: "string" },
    riskLevel: "low",
    sideEffects: false,
    idempotent: true,
    cost: { approxTokens: 0, approxUsd: 0, approxLatencyMs: 500 },
    examples: [{ id: "18e123abc" }],
  },
  {
    name: "send_message",
    description: "Send an email (RFC822). Requires approval by default.",
    actionType: "send",
    inputSchema: { to: "string", subject: "string", body: "string" },
    outputSchema: { id: "string", threadId: "string" },
    riskLevel: "high",
    sideEffects: true,
    idempotent: false,
    cost: { approxTokens: 0, approxUsd: 0, approxLatencyMs: 1200 },
    examples: [],
  },
];

export class GmailConnector extends BaseConnector {
  private readonly auth: GoogleAuth;

  constructor(config: GmailConfig) {
    const descriptor: ConnectorDescriptor = {
      id: newConnectorId(),
      serviceName: "gmail",
      version: "1.0.0",
      transport: "rest",
      authKind: "oauth2",
      capabilities: CAPABILITIES,
      riskLevel: "medium",
      enabled: true,
      health: "unknown",
      tags: ["email", "google"],
      description: "Gmail API v1 connector.",
    };
    super(descriptor);
    this.auth = new GoogleAuth(config);
  }

  protected async invoke(cap: CapabilityDescriptor, req: ConnectorRequest): Promise<unknown> {
    const token = await this.auth.accessToken();
    const headers = { authorization: `Bearer ${token}` };
    switch (cap.name) {
      case "search_messages": {
        const query = String(req.input.query ?? "");
        const maxResults = Number(req.input.maxResults ?? 10);
        const url = `${BASE}/messages?q=${encodeURIComponent(query)}&maxResults=${maxResults}`;
        const res = await request(url, { headers });
        const body = (await res.body.json()) as { messages?: Array<{ id: string; threadId: string }> };
        if (res.statusCode >= 400) throw httpErr(res.statusCode);
        return { items: body.messages ?? [] };
      }
      case "get_message": {
        const id = String(req.input.id);
        const url = `${BASE}/messages/${encodeURIComponent(id)}?format=metadata&metadataHeaders=From&metadataHeaders=Subject`;
        const res = await request(url, { headers });
        if (res.statusCode >= 400) throw httpErr(res.statusCode);
        const msg = (await res.body.json()) as {
          id: string;
          threadId: string;
          snippet: string;
          payload?: { headers: Array<{ name: string; value: string }> };
        };
        const header = (n: string) => msg.payload?.headers.find((h) => h.name === n)?.value ?? "";
        return {
          id: msg.id,
          threadId: msg.threadId,
          from: header("From"),
          subject: header("Subject"),
          snippet: msg.snippet,
        };
      }
      case "send_message": {
        const to = String(req.input.to);
        const subject = String(req.input.subject);
        const body = String(req.input.body);
        const raw = Buffer.from(
          [`To: ${to}`, `Subject: ${subject}`, "Content-Type: text/plain; charset=utf-8", "", body].join("\r\n"),
          "utf-8",
        ).toString("base64url");
        const url = `${BASE}/messages/send`;
        const res = await request(url, {
          method: "POST",
          headers: { ...headers, "content-type": "application/json" },
          body: JSON.stringify({ raw }),
        });
        if (res.statusCode >= 400) throw httpErr(res.statusCode);
        return (await res.body.json()) as { id: string; threadId: string };
      }
      default:
        throw new OrchestratorError("CONNECTOR_NOT_FOUND", `gmail: unknown capability ${cap.name}`);
    }
  }

  protected override async probe(): Promise<void> {
    const token = await this.auth.accessToken();
    const res = await request(`${BASE}/profile`, { headers: { authorization: `Bearer ${token}` } });
    if (res.statusCode >= 400) throw httpErr(res.statusCode);
  }

  protected override async summarize(
    cap: CapabilityDescriptor,
    req: ConnectorRequest,
    output: Record<string, unknown>,
  ): Promise<string> {
    if (cap.name === "search_messages") {
      return `gmail.search q='${String(req.input.query ?? "")}' -> ${(output.items as unknown[])?.length ?? 0} messages`;
    }
    if (cap.name === "send_message") {
      return `gmail.send to=${String(req.input.to)} subject='${String(req.input.subject)}' id=${String(output.id)}`;
    }
    return `gmail.${cap.name}`;
  }
}

function httpErr(code: number) {
  if (code === 401 || code === 403)
    return new OrchestratorError("CONNECTOR_AUTH_FAILED", `gmail ${code}`);
  if (code === 429)
    return new OrchestratorError("CONNECTOR_RATE_LIMITED", `gmail ${code}`, { retryable: true });
  if (code >= 500)
    return new OrchestratorError("CONNECTOR_UPSTREAM_ERROR", `gmail ${code}`, { retryable: true });
  return new OrchestratorError("CONNECTOR_UPSTREAM_ERROR", `gmail ${code}`);
}
