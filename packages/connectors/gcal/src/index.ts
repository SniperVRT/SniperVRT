import { request } from "undici";
import {
  CapabilityDescriptor,
  ConnectorDescriptor,
  ConnectorRequest,
  OrchestratorError,
  newConnectorId,
} from "@snipervrt/shared";
import { BaseConnector } from "@snipervrt/connector-core";
import { GoogleAuth } from "@snipervrt/connector-gmail/oauth";

const BASE = "https://www.googleapis.com/calendar/v3";

export interface GCalConfig {
  clientId: string;
  clientSecret: string;
  refreshToken: string;
  defaultCalendarId?: string;
}

const CAPABILITIES: CapabilityDescriptor[] = [
  {
    name: "list_events",
    description: "List events on a calendar between timeMin and timeMax.",
    actionType: "list",
    inputSchema: { calendarId: "string", timeMin: "datetime", timeMax: "datetime", q: "string" },
    outputSchema: { items: "array" },
    riskLevel: "low",
    sideEffects: false,
    idempotent: true,
    cost: { approxTokens: 0, approxUsd: 0, approxLatencyMs: 600 },
    examples: [{ timeMin: "2026-04-17T00:00:00Z", timeMax: "2026-04-18T00:00:00Z" }],
  },
  {
    name: "create_event",
    description: "Create a calendar event.",
    actionType: "create",
    inputSchema: { calendarId: "string", summary: "string", start: "object", end: "object", attendees: "array" },
    outputSchema: { id: "string", htmlLink: "string" },
    riskLevel: "medium",
    sideEffects: true,
    idempotent: false,
    cost: { approxTokens: 0, approxUsd: 0, approxLatencyMs: 800 },
    examples: [],
  },
  {
    name: "delete_event",
    description: "Delete a calendar event by id.",
    actionType: "delete",
    inputSchema: { calendarId: "string", eventId: "string" },
    outputSchema: { ok: true },
    riskLevel: "high",
    sideEffects: true,
    idempotent: true,
    cost: { approxTokens: 0, approxUsd: 0, approxLatencyMs: 600 },
    examples: [],
  },
];

export class GCalConnector extends BaseConnector {
  private readonly auth: GoogleAuth;

  constructor(private readonly cfg: GCalConfig) {
    super({
      id: newConnectorId(),
      serviceName: "gcal",
      version: "1.0.0",
      transport: "rest",
      authKind: "oauth2",
      capabilities: CAPABILITIES,
      riskLevel: "medium",
      enabled: true,
      health: "unknown",
      tags: ["calendar", "google"],
      description: "Google Calendar v3 connector.",
    });
    this.auth = new GoogleAuth(cfg);
  }

  protected async invoke(cap: CapabilityDescriptor, req: ConnectorRequest): Promise<unknown> {
    const token = await this.auth.accessToken();
    const headers: Record<string, string> = { authorization: `Bearer ${token}` };
    const calendarId = String(req.input.calendarId ?? this.cfg.defaultCalendarId ?? "primary");
    switch (cap.name) {
      case "list_events": {
        const q = String(req.input.q ?? "");
        const timeMin = req.input.timeMin ? `&timeMin=${encodeURIComponent(String(req.input.timeMin))}` : "";
        const timeMax = req.input.timeMax ? `&timeMax=${encodeURIComponent(String(req.input.timeMax))}` : "";
        const url = `${BASE}/calendars/${encodeURIComponent(calendarId)}/events?singleEvents=true&orderBy=startTime&q=${encodeURIComponent(q)}${timeMin}${timeMax}`;
        const res = await request(url, { headers });
        if (res.statusCode >= 400) throw httpErr(res.statusCode);
        const body = (await res.body.json()) as { items?: unknown[] };
        return { items: body.items ?? [] };
      }
      case "create_event": {
        const url = `${BASE}/calendars/${encodeURIComponent(calendarId)}/events`;
        const res = await request(url, {
          method: "POST",
          headers: { ...headers, "content-type": "application/json" },
          body: JSON.stringify({
            summary: req.input.summary,
            start: req.input.start,
            end: req.input.end,
            attendees: req.input.attendees,
            description: req.input.description,
          }),
        });
        if (res.statusCode >= 400) throw httpErr(res.statusCode);
        return await res.body.json();
      }
      case "delete_event": {
        const eventId = String(req.input.eventId);
        const url = `${BASE}/calendars/${encodeURIComponent(calendarId)}/events/${encodeURIComponent(eventId)}`;
        const res = await request(url, { method: "DELETE", headers });
        if (res.statusCode >= 400 && res.statusCode !== 410) throw httpErr(res.statusCode);
        return { ok: true };
      }
      default:
        throw new OrchestratorError("CONNECTOR_NOT_FOUND", `gcal: unknown capability ${cap.name}`);
    }
  }

  protected override async probe(): Promise<void> {
    const token = await this.auth.accessToken();
    const res = await request(`${BASE}/users/me/calendarList?maxResults=1`, {
      headers: { authorization: `Bearer ${token}` },
    });
    if (res.statusCode >= 400) throw httpErr(res.statusCode);
  }
}

function httpErr(code: number) {
  if (code === 401 || code === 403)
    return new OrchestratorError("CONNECTOR_AUTH_FAILED", `gcal ${code}`);
  if (code === 429)
    return new OrchestratorError("CONNECTOR_RATE_LIMITED", `gcal ${code}`, { retryable: true });
  if (code >= 500)
    return new OrchestratorError("CONNECTOR_UPSTREAM_ERROR", `gcal ${code}`, { retryable: true });
  return new OrchestratorError("CONNECTOR_UPSTREAM_ERROR", `gcal ${code}`);
}
