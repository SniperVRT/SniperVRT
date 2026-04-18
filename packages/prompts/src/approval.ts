import { JSON_ONLY_FOOTER, PROMPT_VERSIONS, section, type PromptBundle } from "./shared.js";

export interface ApprovalExplanationInput {
  actionType: string;
  service: string;
  capability: string;
  riskLevel: string;
  redactedInput: unknown;
  policyReason: string;
}

const SYSTEM = [
  "You are the APPROVAL EXPLAINER. Produce a short, honest, human-readable",
  "summary of what a user is about to approve. No hype, no marketing.",
].join("\n");

export function approvalExplanationPrompt(input: ApprovalExplanationInput): PromptBundle {
  const user = [
    section("Proposed action", `${input.service}.${input.capability} [${input.actionType}, risk=${input.riskLevel}]`),
    section("Policy reason", input.policyReason),
    section("Input (redacted)", JSON.stringify(input.redactedInput)),
    "",
    "Output schema:",
    `{
  "title": "string",
  "humanSummary": "string",
  "effects": ["string"],
  "reversible": true,
  "intent": "string"
}`,
    "",
    JSON_ONLY_FOOTER,
  ].join("\n");
  return { version: PROMPT_VERSIONS.approval, system: SYSTEM, user };
}
