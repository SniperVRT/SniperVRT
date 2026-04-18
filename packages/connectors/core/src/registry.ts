import {
  ConnectorDescriptor,
  OrchestratorError,
  TransportKind,
  TRANSPORT_PREFERENCE,
  ActionType,
} from "@snipervrt/shared";
import type { Connector } from "./connector.js";

export interface RegistryEntry {
  connector: Connector;
  // Cached capability summary used by the planner. Refreshed on registration
  // and on health probes so we don't re-call describeCapabilities() per plan.
  capabilitySummary: string;
  lastHealthCheckAt: number;
}

export interface ConnectorSelection {
  connector: Connector;
  reason: string;
  score: number;
}

// Registry is an in-process catalog. For multi-node it should be backed by
// Postgres (connectors table), but the interface stays the same - this
// implementation is swappable.
export class ConnectorRegistry {
  private readonly byId = new Map<string, RegistryEntry>();
  private readonly byService = new Map<string, string[]>();

  register(connector: Connector): void {
    const d = connector.descriptor;
    if (this.byId.has(d.id)) {
      throw new OrchestratorError(
        "CONNECTOR_NOT_FOUND",
        `connector ${d.id} already registered`,
      );
    }
    this.byId.set(d.id, {
      connector,
      capabilitySummary: summarize(d),
      lastHealthCheckAt: 0,
    });
    const list = this.byService.get(d.serviceName) ?? [];
    list.push(d.id);
    this.byService.set(d.serviceName, list);
  }

  unregister(id: string): void {
    const entry = this.byId.get(id);
    if (!entry) return;
    this.byId.delete(id);
    const list = this.byService.get(entry.connector.descriptor.serviceName);
    if (list) {
      const i = list.indexOf(id);
      if (i >= 0) list.splice(i, 1);
    }
  }

  get(id: string): Connector | undefined {
    return this.byId.get(id)?.connector;
  }

  getByService(service: string): Connector[] {
    const ids = this.byService.get(service) ?? [];
    return ids.flatMap((id) => {
      const c = this.byId.get(id)?.connector;
      return c ? [c] : [];
    });
  }

  list(): ConnectorDescriptor[] {
    return Array.from(this.byId.values()).map((e) => e.connector.descriptor);
  }

  describeForPlanner(): string {
    // Compact, Claude-friendly catalog. Small enough to fit many connectors
    // in the planner prompt without dumping full schemas.
    return Array.from(this.byId.values())
      .map((e) => e.capabilitySummary)
      .join("\n\n");
  }

  // Score candidate connectors for a requested capability. Used by the
  // integration agent as a deterministic pre-filter before asking Claude.
  select(opts: {
    preferredService?: string;
    candidateServices?: string[];
    capabilityName?: string;
    actionType?: ActionType;
    forbiddenServices?: string[];
  }): ConnectorSelection[] {
    const {
      preferredService,
      candidateServices = [],
      capabilityName,
      actionType,
      forbiddenServices = [],
    } = opts;
    const forbidden = new Set(forbiddenServices);
    const results: ConnectorSelection[] = [];

    for (const entry of this.byId.values()) {
      const d = entry.connector.descriptor;
      if (!d.enabled) continue;
      if (forbidden.has(d.serviceName)) continue;
      if (d.health === "unhealthy") continue;

      // Capability match check.
      const matchingCap = capabilityName
        ? d.capabilities.find((c) => c.name === capabilityName)
        : actionType
          ? d.capabilities.find((c) => c.actionType === actionType)
          : d.capabilities[0];
      if (!matchingCap) continue;

      let score = 50;
      if (preferredService && d.serviceName === preferredService) score += 40;
      if (candidateServices.includes(d.serviceName)) score += 15;
      score += transportBonus(d.transport);
      if (d.health === "healthy") score += 10;
      if (d.health === "degraded") score -= 5;

      results.push({
        connector: entry.connector,
        score,
        reason: buildReason(d, matchingCap.name, preferredService),
      });
    }
    return results.sort((a, b) => b.score - a.score);
  }

  async checkHealthAll(): Promise<void> {
    const now = Date.now();
    await Promise.all(
      Array.from(this.byId.values()).map(async (entry) => {
        try {
          const h = await entry.connector.healthCheck();
          entry.connector.descriptor.health = h.status;
          entry.lastHealthCheckAt = now;
          entry.capabilitySummary = summarize(entry.connector.descriptor);
        } catch {
          entry.connector.descriptor.health = "unhealthy";
        }
      }),
    );
  }
}

function transportBonus(t: TransportKind): number {
  // Lower index in the preference list → larger bonus.
  const idx = TRANSPORT_PREFERENCE.indexOf(t);
  if (idx < 0) return 0;
  return (TRANSPORT_PREFERENCE.length - idx) * 5;
}

function summarize(d: ConnectorDescriptor): string {
  const caps = d.capabilities
    .map(
      (c) =>
        `  - ${c.name} [${c.actionType}, risk=${c.riskLevel}${c.idempotent ? ", idempotent" : ""}] :: ${c.description}`,
    )
    .join("\n");
  return [
    `### ${d.serviceName} (${d.transport}, auth=${d.authKind}, health=${d.health}, risk=${d.riskLevel})`,
    d.description ? d.description : "",
    caps || "  (no capabilities registered)",
  ]
    .filter(Boolean)
    .join("\n");
}

function buildReason(
  d: ConnectorDescriptor,
  capName: string,
  preferred?: string,
): string {
  const parts = [
    `transport=${d.transport}`,
    `health=${d.health}`,
    preferred && d.serviceName === preferred ? "preferred by goal" : undefined,
    `cap=${capName}`,
  ].filter(Boolean);
  return parts.join(", ");
}
