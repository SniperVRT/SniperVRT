import {
  MemoryEntry,
  MemoryEntrySchema,
  MemoryQuery,
  newMemoryId,
} from "@snipervrt/shared";

export interface MemoryStore {
  put(entry: Omit<MemoryEntry, "id" | "createdAt" | "updatedAt">): Promise<MemoryEntry>;
  get(id: string): Promise<MemoryEntry | null>;
  search(query: MemoryQuery): Promise<MemoryEntry[]>;
  markUsed(id: string, usefulnessDelta?: number): Promise<void>;
  prune(maxAgeMs: number, now?: Date): Promise<number>;
}

// In-memory reference impl. Search uses tag overlap + text scoring. Good enough
// for small memories and tests. Swap for pgvector or similar in production by
// re-implementing this interface.
export class InMemoryMemoryStore implements MemoryStore {
  private readonly byId = new Map<string, MemoryEntry>();

  async put(entry: Omit<MemoryEntry, "id" | "createdAt" | "updatedAt">) {
    const now = new Date().toISOString();
    const parsed = MemoryEntrySchema.parse({
      id: newMemoryId(),
      createdAt: now,
      updatedAt: now,
      ...entry,
    });
    this.byId.set(parsed.id, parsed);
    return parsed;
  }

  async get(id: string) {
    return this.byId.get(id) ?? null;
  }

  async search(query: MemoryQuery) {
    const candidates = Array.from(this.byId.values()).filter((m) => {
      if (query.scope && m.scope !== query.scope) return false;
      if (query.kinds && !query.kinds.includes(m.kind)) return false;
      if (query.tenantId && m.tenantId && m.tenantId !== query.tenantId) return false;
      if (query.userId && m.userId && m.userId !== query.userId) return false;
      if (query.taskId && m.taskId && m.taskId !== query.taskId) return false;
      if (query.tags && query.tags.length > 0) {
        if (!query.tags.some((t) => m.tags.includes(t))) return false;
      }
      return true;
    });
    const scored = candidates
      .map((m) => ({ m, score: scoreEntry(m, query.text) }))
      .sort((a, b) => b.score - a.score)
      .slice(0, query.limit ?? 10)
      .map((x) => x.m);
    return scored;
  }

  async markUsed(id: string, usefulnessDelta = 0.02) {
    const m = this.byId.get(id);
    if (!m) return;
    const updated: MemoryEntry = {
      ...m,
      useCount: m.useCount + 1,
      usefulness: Math.max(0, Math.min(1, m.usefulness + usefulnessDelta)),
      updatedAt: new Date().toISOString(),
    };
    this.byId.set(id, updated);
  }

  async prune(maxAgeMs: number, now: Date = new Date()) {
    const cutoff = now.getTime() - maxAgeMs;
    let n = 0;
    for (const [id, m] of this.byId.entries()) {
      if (m.expiresAt && new Date(m.expiresAt).getTime() < now.getTime()) {
        this.byId.delete(id);
        n++;
        continue;
      }
      if (new Date(m.updatedAt).getTime() < cutoff && m.usefulness < 0.1) {
        this.byId.delete(id);
        n++;
      }
    }
    return n;
  }
}

function scoreEntry(m: MemoryEntry, text?: string): number {
  let score = m.usefulness * 10 + m.confidence * 5;
  if (text) {
    const hay = `${m.title} ${m.body} ${m.tags.join(" ")}`.toLowerCase();
    const needle = text.toLowerCase();
    const words = needle.split(/\s+/).filter(Boolean);
    for (const w of words) {
      if (hay.includes(w)) score += 2;
    }
  }
  return score;
}
