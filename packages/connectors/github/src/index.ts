import { request } from "undici";
import {
  CapabilityDescriptor,
  ConnectorRequest,
  OrchestratorError,
  newConnectorId,
} from "@snipervrt/shared";
import { BaseConnector } from "@snipervrt/connector-core";

const BASE = "https://api.github.com";

export interface GitHubConfig {
  token: string;
}

const CAPABILITIES: CapabilityDescriptor[] = [
  {
    name: "search_repos",
    description: "Search public repositories.",
    actionType: "search",
    inputSchema: { q: "string", per_page: "number" },
    outputSchema: { items: "array" },
    riskLevel: "low",
    sideEffects: false,
    idempotent: true,
    cost: { approxTokens: 0, approxUsd: 0, approxLatencyMs: 600 },
    examples: [{ q: "org:anthropics language:python" }],
  },
  {
    name: "list_issues",
    description: "List issues for owner/repo.",
    actionType: "list",
    inputSchema: { owner: "string", repo: "string", state: "string" },
    outputSchema: { items: "array" },
    riskLevel: "low",
    sideEffects: false,
    idempotent: true,
    cost: { approxTokens: 0, approxUsd: 0, approxLatencyMs: 500 },
    examples: [],
  },
  {
    name: "create_issue",
    description: "Create a new issue in owner/repo.",
    actionType: "create",
    inputSchema: { owner: "string", repo: "string", title: "string", body: "string", labels: "array" },
    outputSchema: { id: "number", html_url: "string" },
    riskLevel: "medium",
    sideEffects: true,
    idempotent: false,
    cost: { approxTokens: 0, approxUsd: 0, approxLatencyMs: 800 },
    examples: [],
  },
  {
    name: "comment_issue",
    description: "Post a comment to an issue or PR.",
    actionType: "send",
    inputSchema: { owner: "string", repo: "string", issue_number: "number", body: "string" },
    outputSchema: { id: "number", html_url: "string" },
    riskLevel: "high",
    sideEffects: true,
    idempotent: false,
    cost: { approxTokens: 0, approxUsd: 0, approxLatencyMs: 700 },
    examples: [],
  },
];

export class GitHubConnector extends BaseConnector {
  constructor(private readonly cfg: GitHubConfig) {
    super({
      id: newConnectorId(),
      serviceName: "github",
      version: "1.0.0",
      transport: "rest",
      authKind: "bearer",
      capabilities: CAPABILITIES,
      riskLevel: "medium",
      enabled: true,
      health: "unknown",
      tags: ["git", "github"],
      description: "GitHub REST v3 connector.",
    });
  }

  private headers() {
    return {
      authorization: `Bearer ${this.cfg.token}`,
      accept: "application/vnd.github+json",
      "x-github-api-version": "2022-11-28",
      "content-type": "application/json",
      "user-agent": "snipervrt/0.1",
    };
  }

  protected async invoke(cap: CapabilityDescriptor, req: ConnectorRequest): Promise<unknown> {
    const h = this.headers();
    switch (cap.name) {
      case "search_repos": {
        const q = encodeURIComponent(String(req.input.q ?? ""));
        const per = Number(req.input.per_page ?? 10);
        const res = await request(`${BASE}/search/repositories?q=${q}&per_page=${per}`, { headers: h });
        if (res.statusCode >= 400) throw httpErr(res.statusCode);
        const body = (await res.body.json()) as { items: unknown[] };
        return { items: body.items };
      }
      case "list_issues": {
        const { owner, repo } = req.input as { owner: string; repo: string };
        const state = String(req.input.state ?? "open");
        const res = await request(
          `${BASE}/repos/${owner}/${repo}/issues?state=${state}&per_page=20`,
          { headers: h },
        );
        if (res.statusCode >= 400) throw httpErr(res.statusCode);
        return { items: (await res.body.json()) as unknown[] };
      }
      case "create_issue": {
        const { owner, repo } = req.input as { owner: string; repo: string };
        const res = await request(`${BASE}/repos/${owner}/${repo}/issues`, {
          method: "POST",
          headers: h,
          body: JSON.stringify({
            title: req.input.title,
            body: req.input.body,
            labels: req.input.labels,
          }),
        });
        if (res.statusCode >= 400) throw httpErr(res.statusCode);
        return await res.body.json();
      }
      case "comment_issue": {
        const { owner, repo, issue_number } = req.input as {
          owner: string;
          repo: string;
          issue_number: number;
        };
        const res = await request(
          `${BASE}/repos/${owner}/${repo}/issues/${issue_number}/comments`,
          { method: "POST", headers: h, body: JSON.stringify({ body: req.input.body }) },
        );
        if (res.statusCode >= 400) throw httpErr(res.statusCode);
        return await res.body.json();
      }
      default:
        throw new OrchestratorError("CONNECTOR_NOT_FOUND", `github: unknown ${cap.name}`);
    }
  }

  protected override async probe(): Promise<void> {
    const res = await request(`${BASE}/user`, { headers: this.headers() });
    if (res.statusCode >= 400) throw httpErr(res.statusCode);
  }
}

function httpErr(code: number) {
  if (code === 401) return new OrchestratorError("CONNECTOR_AUTH_FAILED", `github ${code}`);
  if (code === 403 || code === 429)
    return new OrchestratorError("CONNECTOR_RATE_LIMITED", `github ${code}`, { retryable: true });
  if (code >= 500)
    return new OrchestratorError("CONNECTOR_UPSTREAM_ERROR", `github ${code}`, { retryable: true });
  return new OrchestratorError("CONNECTOR_UPSTREAM_ERROR", `github ${code}`);
}
