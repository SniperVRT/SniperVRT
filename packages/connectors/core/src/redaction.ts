import { createHash } from "node:crypto";

// Secrets never hit logs or Claude context. Payloads we keep for audit are
// hashed so we can correlate without exposing bodies.
const REDACTED = "[REDACTED]";

const SECRET_KEY_PATTERNS = [
  /token/i,
  /secret/i,
  /password/i,
  /api[_-]?key/i,
  /authorization/i,
  /cookie/i,
  /refresh[_-]?token/i,
  /client[_-]?secret/i,
  /private[_-]?key/i,
];

export function redact<T>(value: T): T {
  return redactInternal(value, new WeakSet()) as T;
}

function redactInternal(value: unknown, seen: WeakSet<object>): unknown {
  if (value === null || typeof value !== "object") return value;
  if (seen.has(value as object)) return "[CIRCULAR]";
  seen.add(value as object);

  if (Array.isArray(value)) return value.map((v) => redactInternal(v, seen));

  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    if (SECRET_KEY_PATTERNS.some((p) => p.test(k))) {
      out[k] = REDACTED;
    } else {
      out[k] = redactInternal(v, seen);
    }
  }
  return out;
}

export function hashPayload(value: unknown): string {
  const json = JSON.stringify(value ?? null);
  return createHash("sha256").update(json).digest("hex").slice(0, 32);
}
