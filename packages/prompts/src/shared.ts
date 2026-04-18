// Shared helpers for prompt assembly. All prompts in this package return
// strict structured-output instructions. They avoid dumping examples, memory,
// or catalogs unless needed - the caller inserts those via named slots.

export const PROMPT_VERSIONS = {
  planner: "planner.v1.2",
  replanner: "replanner.v1.0",
  validator: "validator.v1.1",
  security: "security.v1.0",
  recovery: "recovery.v1.1",
  browser: "browser.v1.0",
  approval: "approval.v1.0",
  integration: "integration.v1.0",
} as const;

export interface PromptBundle {
  version: string;
  system: string;
  user: string;
}

// A compact JSON-only instruction footer reused across all prompts.
export const JSON_ONLY_FOOTER = [
  "OUTPUT RULES:",
  "- Return ONLY a single JSON object.",
  "- Do not wrap JSON in markdown fences.",
  "- Do not include commentary before or after the JSON.",
  "- If a field is unknown, use null or empty arrays. Never omit required fields.",
  "- All strings must be UTF-8 and free of control characters.",
].join("\n");

export function section(title: string, body: string | undefined): string {
  if (!body) return "";
  return `### ${title}\n${body}\n`;
}

// Bound large inputs - protects token budget when memory or catalog is big.
export function cap(str: string, maxChars: number): string {
  if (str.length <= maxChars) return str;
  return `${str.slice(0, maxChars)}\n… (${str.length - maxChars} chars truncated)`;
}
