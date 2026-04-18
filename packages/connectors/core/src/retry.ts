import type { RetryStrategy } from "@snipervrt/shared";

// Shared retry loop. Connectors and the executor both use this so retries
// look the same in traces everywhere.
export async function withRetry<T>(
  fn: (attempt: number) => Promise<T>,
  strategy: RetryStrategy,
  isRetryable: (err: unknown) => boolean = () => true,
  onRetry?: (err: unknown, attempt: number, waitMs: number) => void,
): Promise<T> {
  let lastErr: unknown;
  for (let attempt = 0; attempt <= strategy.maxAttempts; attempt++) {
    try {
      return await fn(attempt);
    } catch (err) {
      lastErr = err;
      if (attempt === strategy.maxAttempts || !isRetryable(err)) break;
      const wait = Math.round(strategy.backoffMs * strategy.backoffMultiplier ** attempt);
      onRetry?.(err, attempt, wait);
      await sleep(wait);
    }
  }
  throw lastErr;
}

function sleep(ms: number): Promise<void> {
  return new Promise((res) => setTimeout(res, ms));
}
