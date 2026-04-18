import { JSON_ONLY_FOOTER, PROMPT_VERSIONS, cap, section, type PromptBundle } from "./shared.js";

export interface ValidatorPromptInput {
  stepDescription: string;
  successCondition: string;
  expectedOutputSchema?: Record<string, unknown>;
  rawOutput: unknown;
  constraints: string[];
}

const SYSTEM = [
  "You are the VALIDATOR. Given a step's success condition and its actual output,",
  "decide whether the step succeeded. Be strict. Prefer rejecting ambiguous output.",
  "",
  "RULES:",
  "- You NEVER re-run the step. You only judge.",
  "- Prefer structured signals (fields, IDs, counts) over inferring meaning from prose.",
  "- If the output is malformed or partially corrupt, reject it.",
  "- Provide a short reason. Provide confidence in [0,1].",
  "- If retry is clearly futile, set escalate=true.",
].join("\n");

export function validatorPrompt(input: ValidatorPromptInput): PromptBundle {
  const user = [
    section("Step description", input.stepDescription),
    section("Success condition", input.successCondition),
    section("Expected output schema", input.expectedOutputSchema ? JSON.stringify(input.expectedOutputSchema) : undefined),
    section("Constraints", input.constraints.join("\n")),
    section("Actual output", cap(JSON.stringify(input.rawOutput), 4000)),
    "",
    "Output schema:",
    `{
  "passed": true,
  "confidence": 0.9,
  "reason": "string",
  "issues": ["string"],
  "escalate": false,
  "suggestedFix": "string|null"
}`,
    "",
    JSON_ONLY_FOOTER,
  ]
    .filter(Boolean)
    .join("\n");
  return { version: PROMPT_VERSIONS.validator, system: SYSTEM, user };
}
