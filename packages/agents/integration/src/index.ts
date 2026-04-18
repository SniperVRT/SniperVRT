import { PlanStep, OrchestratorError } from "@snipervrt/shared";
import type { ClaudeClient } from "@snipervrt/planner";
import { integrationPrompt } from "@snipervrt/prompts";
import type { Connector } from "@snipervrt/connector-core";
import { ConnectorRegistry } from "@snipervrt/connector-core";

export interface IntegrationSelection {
  connector: Connector;
  capability: string;
  payload: Record<string, unknown>;
  confidence: number;
  missingFields: string[];
}

export interface IntegrationAgentOptions {
  model: string;
  registry: ConnectorRegistry;
  client: ClaudeClient;
}

// The INTEGRATION agent maps a plan step to a concrete (connector, capability,
// payload). It uses the registry to pre-filter candidates so the model only
// sees a small, relevant slice of the catalog.
export class IntegrationAgent {
  constructor(private readonly opts: IntegrationAgentOptions) {}

  async select(step: PlanStep, constraints: string[]): Promise<IntegrationSelection> {
    // Step 1: deterministic pre-filter via the registry.
    const ranked = this.opts.registry.select({
      preferredService: step.preferredConnector ?? undefined,
      candidateServices: step.candidateConnectors,
      actionType: step.actionType,
    });
    if (ranked.length === 0) {
      throw new OrchestratorError(
        "CONNECTOR_NOT_FOUND",
        `no connector supports action ${step.actionType} for step ${step.id}`,
      );
    }

    // If there's a clear deterministic winner we skip the model entirely.
    const top = ranked[0]!;
    const onlyOne = ranked.length === 1 || (ranked[1] && top.score - ranked[1].score > 25);
    if (onlyOne) {
      const capName = this.pickCap(top.connector, step);
      return {
        connector: top.connector,
        capability: capName,
        payload: { ...step.inputTemplate },
        confidence: step.confidence,
        missingFields: [],
      };
    }

    // Step 2: ask Claude to finalize payload from a short menu.
    const summary = ranked
      .slice(0, 5)
      .map((r) => `- ${r.connector.descriptor.serviceName} (${r.reason}) caps: ${r.connector.descriptor.capabilities.map((c) => c.name).join(", ")}`)
      .join("\n");

    const { data } = await this.opts.client.callJson<{
      connectorId: string;
      capability: string;
      payload: Record<string, unknown>;
      missingFields: string[];
      confidence: number;
    }>(this.opts.model, integrationPrompt({
      stepDescription: step.description,
      stepInput: step.inputTemplate,
      selectedConnectors: summary,
      constraints,
    }));

    const picked = ranked.find((r) => r.connector.descriptor.id === data.connectorId || r.connector.descriptor.serviceName === data.connectorId) ?? top;
    return {
      connector: picked.connector,
      capability: data.capability ?? this.pickCap(picked.connector, step),
      payload: data.payload ?? step.inputTemplate,
      confidence: data.confidence ?? step.confidence,
      missingFields: data.missingFields ?? [],
    };
  }

  private pickCap(connector: Connector, step: PlanStep): string {
    const byName = step.candidateConnectors.length > 0
      ? connector.descriptor.capabilities.find((c) => c.actionType === step.actionType)
      : undefined;
    if (byName) return byName.name;
    const byAction = connector.descriptor.capabilities.find((c) => c.actionType === step.actionType);
    return byAction?.name ?? (connector.descriptor.capabilities[0]?.name ?? "unknown");
  }
}
