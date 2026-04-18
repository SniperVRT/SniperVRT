import { describe, it, expect } from "vitest";
import { GoalSchema, PlanSchema } from "../../packages/shared/src/index.js";
import { ScriptedClaudeClient, Planner, extractJson, applyPatch } from "../../packages/planner/src/index.js";

describe("Planner", () => {
  it("produces a zod-valid Plan from scripted Claude output", async () => {
    const client = new ScriptedClaudeClient(
      new Map([
        [
          "planner.v1.2",
          () => ({
            version: 1,
            objective: "Summarize yesterday's Gmail inbox",
            summary: "Read + summarize",
            assumptions: ["gmail connector healthy"],
            constraints: [],
            requiredServices: ["gmail"],
            optionalServices: [],
            steps: [
              {
                id: "s1",
                description: "List yesterday's messages",
                assignedAgent: "execution",
                preferredConnector: "gmail",
                candidateConnectors: ["gmail"],
                actionType: "list",
                riskLevel: "low",
                dependsOn: [],
                inputTemplate: { query: "newer_than:1d" },
                successCondition: "output.items is an array",
                confidence: 0.8,
              },
              {
                id: "s2",
                description: "Summarize them",
                assignedAgent: "execution",
                preferredConnector: null,
                actionType: "summarize",
                riskLevel: "low",
                dependsOn: ["s1"],
                successCondition: "output.summary is a string",
                confidence: 0.8,
              },
            ],
            overallRisk: "low",
            confidence: 0.9,
          }),
        ],
      ]),
    );
    const planner = new Planner(client, { model: "claude-opus-4-7" });
    const goal = GoalSchema.parse({ objective: "Summarize yesterday's Gmail inbox" });
    const plan = await planner.plan(goal, {
      connectorCatalog: "### gmail (rest, ...)\n  - list [list, risk=low] :: list messages",
      executionMode: "normal",
      approvalGate: "high",
    });
    expect(PlanSchema.safeParse(plan).success).toBe(true);
    expect(plan.steps.length).toBe(2);
  });

  it("extractJson handles markdown-fenced output", () => {
    const v = extractJson("```json\n{\"x\":1}\n```");
    expect(v).toEqual({ x: 1 });
  });

  it("applyPatch inserts new steps after the given id", () => {
    const plan = PlanSchema.parse({
      objective: "o",
      summary: "s",
      steps: [
        { id: "s1", description: "a", actionType: "read", successCondition: "x" },
        { id: "s2", description: "b", actionType: "read", successCondition: "x" },
      ],
    });
    const patched = applyPatch(plan, {
      reason: "x",
      replaceStepIds: [],
      insertAfter: "s1",
      newSteps: [
        { id: "s1b", description: "injected", actionType: "read", successCondition: "y" } as never,
      ],
      markCompletedStepIds: [],
      abort: false,
    });
    expect(patched.steps.map((s) => s.id)).toEqual(["s1", "s1b", "s2"]);
  });
});
