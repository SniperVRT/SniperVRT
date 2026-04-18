import { JSON_ONLY_FOOTER, PROMPT_VERSIONS, section, type PromptBundle } from "./shared.js";

export interface SecurityPromptInput {
  actionType: string;
  service: string;
  capability: string;
  riskLevel: string;
  intent: string;
  // Compact, redacted view of the payload.
  redactedInput: unknown;
  relevantPolicies: string; // pre-serialized matched rules
  mode: string;
}

const SYSTEM = [
  "You are the SECURITY agent. Given a proposed action and matched policies,",
  "recommend one of: allow, require_approval, deny.",
  "",
  "RULES:",
  "- Deny irreversible actions unless explicitly allowed.",
  "- Treat ambiguous intent as require_approval.",
  "- Never leak sensitive field names in your reason.",
  "- Your decision does not override a declarative deny rule - the engine will enforce that.",
].join("\n");

export function securityPrompt(input: SecurityPromptInput): PromptBundle {
  const user = [
    section("Action", `${input.service}.${input.capability} [${input.actionType}, risk=${input.riskLevel}]`),
    section("Mode", input.mode),
    section("Intent", input.intent),
    section("Matched policies", input.relevantPolicies),
    section("Redacted input", JSON.stringify(input.redactedInput)),
    "",
    "Output schema:",
    `{
  "outcome": "allow|require_approval|deny",
  "reason": "string",
  "confidence": 0.8,
  "approvalHumanSummary": "string|null"
}`,
    "",
    JSON_ONLY_FOOTER,
  ].join("\n");
  return { version: PROMPT_VERSIONS.security, system: SYSTEM, user };
}
