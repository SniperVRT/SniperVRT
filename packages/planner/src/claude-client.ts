import type { PromptBundle } from "@snipervrt/prompts";
import { OrchestratorError } from "@snipervrt/shared";
import { Budget, estimateUsd } from "./budget.js";
import { extractJson } from "./json-extract.js";

// A thin Claude client designed for:
// - structured JSON output (not chat)
// - per-call budget accounting
// - model routing
// - deterministic, injectable fake for tests

export interface ClaudeCallOptions {
  maxTokens?: number;
  temperature?: number;
  stopSequences?: string[];
  timeoutMs?: number;
}

export interface ClaudeCallResult<T> {
  data: T;
  raw: string;
  usage: { inputTokens: number; outputTokens: number; usd: number };
  model: string;
}

export interface ClaudeClient {
  callJson<T>(
    model: string,
    bundle: PromptBundle,
    opts?: ClaudeCallOptions,
  ): Promise<ClaudeCallResult<T>>;
}

// Real implementation backed by @anthropic-ai/sdk. We lazy-import so the
// package stays optional at runtime for tests and so the MCP-only path works
// even without the anthropic SDK installed.
export class AnthropicClaudeClient implements ClaudeClient {
  constructor(
    private readonly opts: { apiKey?: string; budget?: Budget } = {},
  ) {}

  async callJson<T>(
    model: string,
    bundle: PromptBundle,
    opts: ClaudeCallOptions = {},
  ): Promise<ClaudeCallResult<T>> {
    const { default: Anthropic } = await import("@anthropic-ai/sdk");
    const client = new Anthropic({
      apiKey: this.opts.apiKey ?? process.env.ANTHROPIC_API_KEY,
    });

    const response = await client.messages.create({
      model,
      max_tokens: opts.maxTokens ?? 4096,
      temperature: opts.temperature ?? 0,
      system: bundle.system,
      stop_sequences: opts.stopSequences,
      messages: [{ role: "user", content: bundle.user }],
    });

    const text =
      response.content
        .map((c) => (c.type === "text" ? c.text : ""))
        .join("")
        .trim();

    const inputTokens = response.usage?.input_tokens ?? 0;
    const outputTokens = response.usage?.output_tokens ?? 0;
    const usd = estimateUsd(model, inputTokens, outputTokens);
    this.opts.budget?.record(inputTokens + outputTokens, usd);

    const data = extractJson(text) as T;
    return {
      data,
      raw: text,
      usage: { inputTokens, outputTokens, usd },
      model,
    };
  }
}

// Test double: deterministic scripted replies keyed by prompt version.
export class ScriptedClaudeClient implements ClaudeClient {
  constructor(
    private readonly scripts: Map<string, (bundle: PromptBundle) => unknown>,
    private readonly budget?: Budget,
  ) {}

  async callJson<T>(
    model: string,
    bundle: PromptBundle,
  ): Promise<ClaudeCallResult<T>> {
    const handler = this.scripts.get(bundle.version);
    if (!handler) {
      throw new OrchestratorError("PLANNER_UNSUPPORTED", `no script for ${bundle.version}`);
    }
    const data = handler(bundle) as T;
    const raw = JSON.stringify(data);
    const inputTokens = Math.ceil(bundle.user.length / 4);
    const outputTokens = Math.ceil(raw.length / 4);
    const usd = estimateUsd(model, inputTokens, outputTokens);
    this.budget?.record(inputTokens + outputTokens, usd);
    return { data, raw, usage: { inputTokens, outputTokens, usd }, model };
  }
}
