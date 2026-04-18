import type { MemoryEntry } from "@snipervrt/shared";

// Compose retrieved memory into a compact, Claude-ready text block.
// This is what the planner reads - we MUST NOT dump raw JSON into prompts.
export function summarizeMemoryForPrompt(entries: MemoryEntry[]): string {
  if (entries.length === 0) return "";
  const byKind = new Map<string, MemoryEntry[]>();
  for (const e of entries) {
    const arr = byKind.get(e.kind) ?? [];
    arr.push(e);
    byKind.set(e.kind, arr);
  }
  const sections: string[] = [];
  for (const [kind, items] of byKind.entries()) {
    sections.push(`- ${kind}:`);
    for (const it of items) {
      sections.push(`  * ${it.title}: ${it.body}`);
    }
  }
  return sections.join("\n");
}
