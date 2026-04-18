import { JSON_ONLY_FOOTER, PROMPT_VERSIONS, section, type PromptBundle } from "./shared.js";

export interface RecoveryPromptInput {
  failedStepDescription: string;
  failureClass: string;
  failureMessage: string;
  priorAttempts: Array<{ attempt: number; error: string }>;
  availableFallbacks: string[];
  stepInputSummary: string;
}

const SYSTEM = [
  "You are the RECOVERY agent. Given a failed step and its error, choose one of:",
  "retry_same, retry_alt_connector, patch_plan, escalate, abort.",
  "",
  "- retry_same only if the error is plausibly transient.",
  "- retry_alt_connector only if a real alternative is listed.",
  "- patch_plan if the step's intent can be achieved differently.",
  "- escalate if human input is needed.",
  "- abort if continuing would do harm or waste budget.",
].join("\n");

export function recoveryPrompt(input: RecoveryPromptInput): PromptBundle {
  const user = [
    section("Failed step", input.failedStepDescription),
    section("Failure class", input.failureClass),
    section("Failure message", input.failureMessage),
    section("Prior attempts", input.priorAttempts.map((a) => `#${a.attempt}: ${a.error}`).join("\n")),
    section("Available fallback connectors", input.availableFallbacks.join(", ")),
    section("Step input summary", input.stepInputSummary),
    "",
    "Output schema:",
    `{
  "decision": "retry_same|retry_alt_connector|patch_plan|escalate|abort",
  "alternateConnector": "string|null",
  "reason": "string",
  "confidence": 0.7,
  "patchHint": "string|null"
}`,
    "",
    JSON_ONLY_FOOTER,
  ].join("\n");
  return { version: PROMPT_VERSIONS.recovery, system: SYSTEM, user };
}
