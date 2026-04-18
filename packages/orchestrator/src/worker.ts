import type { TaskStore } from "@snipervrt/state";
import type { Orchestrator } from "./orchestrator.js";

// Worker loop. In-process, cooperative. For multi-host deployments, back this
// with Redis/BullMQ - the logic here stays identical.
export interface WorkerOptions {
  pollIntervalMs?: number;
  maxIterations?: number;
  signal?: AbortSignal;
}

export class Worker {
  constructor(
    private readonly orchestrator: Orchestrator,
    private readonly tasks: TaskStore,
  ) {}

  async runOnce(): Promise<boolean> {
    const claimed = await this.tasks.claimNextReady();
    if (!claimed) return false;
    await this.orchestrator.executeToCompletion(claimed.id);
    return true;
  }

  async run(opts: WorkerOptions = {}): Promise<void> {
    const poll = opts.pollIntervalMs ?? 500;
    const max = opts.maxIterations ?? Number.POSITIVE_INFINITY;
    let i = 0;
    while (i < max) {
      if (opts.signal?.aborted) return;
      const worked = await this.runOnce();
      if (!worked) await sleep(poll);
      i++;
    }
  }
}

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}
