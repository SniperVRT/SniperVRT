// Lightweight in-process metrics. Pluggable to Prometheus / OTel later via
// the same interface - nothing else in the codebase cares about the backend.

type Labels = Record<string, string | number>;

export interface MetricsSink {
  increment(name: string, value?: number, labels?: Labels): void;
  observe(name: string, value: number, labels?: Labels): void;
  gauge(name: string, value: number, labels?: Labels): void;
}

class InMemoryMetrics implements MetricsSink {
  readonly counters = new Map<string, number>();
  readonly histograms = new Map<string, number[]>();
  readonly gauges = new Map<string, number>();

  increment(name: string, value = 1, labels?: Labels) {
    const k = this.key(name, labels);
    this.counters.set(k, (this.counters.get(k) ?? 0) + value);
  }
  observe(name: string, value: number, labels?: Labels) {
    const k = this.key(name, labels);
    const arr = this.histograms.get(k) ?? [];
    arr.push(value);
    this.histograms.set(k, arr);
  }
  gauge(name: string, value: number, labels?: Labels) {
    this.gauges.set(this.key(name, labels), value);
  }
  private key(name: string, labels?: Labels) {
    if (!labels) return name;
    const parts = Object.entries(labels)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([k, v]) => `${k}=${v}`);
    return `${name}{${parts.join(",")}}`;
  }
  snapshot() {
    return {
      counters: Object.fromEntries(this.counters),
      gauges: Object.fromEntries(this.gauges),
      histograms: Object.fromEntries(
        Array.from(this.histograms.entries()).map(([k, v]) => [
          k,
          {
            count: v.length,
            sum: v.reduce((a, b) => a + b, 0),
            p50: percentile(v, 50),
            p95: percentile(v, 95),
            p99: percentile(v, 99),
          },
        ]),
      ),
    };
  }
}

function percentile(arr: number[], p: number): number {
  if (arr.length === 0) return 0;
  const s = [...arr].sort((a, b) => a - b);
  const idx = Math.min(s.length - 1, Math.max(0, Math.floor((p / 100) * s.length)));
  return s[idx] as number;
}

export const metrics: InMemoryMetrics = new InMemoryMetrics();
