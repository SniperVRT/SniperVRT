import { Goal, Plan } from "@snipervrt/shared";
import type { Planner } from "@snipervrt/planner";
import type { MemoryStore } from "@snipervrt/memory";
import { summarizeMemoryForPrompt } from "@snipervrt/memory";
import type { ConnectorRegistry } from "@snipervrt/connector-core";

// Thin wrapper. The PLANNING agent is responsible for assembling the
// PlannerContext from memory + registry + goal. Keeping this logic out of the
// orchestrator keeps the orchestrator free of prompt-assembly details.
export class PlanningAgent {
  constructor(
    private readonly planner: Planner,
    private readonly memory: MemoryStore,
    private readonly registry: ConnectorRegistry,
  ) {}

  async plan(goal: Goal, opts: { executionMode: string; approvalGate: string }): Promise<Plan> {
    const memoryEntries = await this.memory.search({
      scope: goal.tenantId ? "tenant" : "global",
      tenantId: goal.tenantId,
      userId: goal.userId,
      text: goal.objective,
      kinds: ["workflow_template", "connector_quirk", "field_mapping", "recovery_note", "user_preference"],
      limit: 8,
    });
    const memorySummary = summarizeMemoryForPrompt(memoryEntries);
    const connectorCatalog = this.registry.describeForPlanner();
    return this.planner.plan(goal, {
      connectorCatalog,
      memorySummary: memorySummary || undefined,
      executionMode: opts.executionMode,
      approvalGate: opts.approvalGate,
    });
  }
}
