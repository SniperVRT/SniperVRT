import { JSON_ONLY_FOOTER, PROMPT_VERSIONS, cap, section, type PromptBundle } from "./shared.js";

export interface IntegrationPromptInput {
  stepDescription: string;
  stepInput: Record<string, unknown>;
  selectedConnectors: string; // summary block of the top-k candidates
  constraints: string[];
}

const SYSTEM = [
  "You are the INTEGRATION agent. Given a step and a short list of candidate connectors,",
  "pick ONE connector and produce a fully-shaped payload for its capability.",
  "",
  "- Use the connector's declared input schema.",
  "- If the step lacks information, mark missingFields and propose a resolution.",
  "- Never invent IDs or tokens.",
  "- If multiple connectors fit, pick the one with lowest risk and highest health.",
].join("\n");

export function integrationPrompt(input: IntegrationPromptInput): PromptBundle {
  const user = [
    section("Step description", input.stepDescription),
    section("Step input (from planner)", JSON.stringify(input.stepInput)),
    section("Candidate connectors", cap(input.selectedConnectors, 4000)),
    section("Constraints", input.constraints.join("\n")),
    "",
    "Output schema:",
    `{
  "connectorId": "string",
  "capability": "string",
  "payload": {},
  "missingFields": ["string"],
  "resolution": "string|null",
  "confidence": 0.8
}`,
    "",
    JSON_ONLY_FOOTER,
  ].join("\n");
  return { version: PROMPT_VERSIONS.integration, system: SYSTEM, user };
}
