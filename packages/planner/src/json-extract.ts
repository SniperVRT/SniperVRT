import { OrchestratorError } from "@snipervrt/shared";

// Claude is told to emit raw JSON, but defend against stray fencing or
// preamble text. We extract the first balanced JSON object from the response.
export function extractJson(text: string): unknown {
  const trimmed = stripFences(text).trim();
  const firstBrace = trimmed.indexOf("{");
  if (firstBrace < 0) {
    throw new OrchestratorError("PLANNER_PARSE_FAILED", "no JSON object in response", {
      details: { snippet: trimmed.slice(0, 200) },
    });
  }
  const candidate = balancedSlice(trimmed, firstBrace);
  try {
    return JSON.parse(candidate);
  } catch (e) {
    throw new OrchestratorError("PLANNER_PARSE_FAILED", "invalid JSON", {
      details: { snippet: candidate.slice(0, 200) },
      cause: e,
    });
  }
}

function stripFences(s: string): string {
  const fence = /^```(?:json)?\s*([\s\S]*?)\s*```$/m;
  const m = s.match(fence);
  return m ? (m[1] ?? s) : s;
}

function balancedSlice(s: string, start: number): string {
  let depth = 0;
  let inString = false;
  let escape = false;
  for (let i = start; i < s.length; i++) {
    const ch = s[i];
    if (inString) {
      if (escape) {
        escape = false;
      } else if (ch === "\\") {
        escape = true;
      } else if (ch === '"') {
        inString = false;
      }
      continue;
    }
    if (ch === '"') {
      inString = true;
      continue;
    }
    if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth === 0) return s.slice(start, i + 1);
    }
  }
  throw new OrchestratorError("PLANNER_PARSE_FAILED", "unbalanced JSON braces", {
    details: { start },
  });
}
