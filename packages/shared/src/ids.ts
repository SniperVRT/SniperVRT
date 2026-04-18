import { randomBytes } from "node:crypto";

// Prefixed, k-sortable ids. Not UUIDs - shorter, self-describing in logs.
// Format: <prefix>_<base36 timestamp>_<12 char random>
export type Prefixed<P extends string> = `${P}_${string}`;

export type TaskId = Prefixed<"tsk">;
export type StepId = Prefixed<"stp">;
export type TraceId = Prefixed<"trc">;
export type ApprovalId = Prefixed<"apv">;
export type ConnectorId = Prefixed<"cxn">;
export type MemoryId = Prefixed<"mem">;
export type RunId = Prefixed<"run">;

function mintId<P extends string>(prefix: P): Prefixed<P> {
  const ts = Date.now().toString(36);
  const rnd = randomBytes(6).toString("hex");
  return `${prefix}_${ts}_${rnd}` as Prefixed<P>;
}

export const newTaskId = (): TaskId => mintId("tsk");
export const newStepId = (): StepId => mintId("stp");
export const newTraceId = (): TraceId => mintId("trc");
export const newApprovalId = (): ApprovalId => mintId("apv");
export const newConnectorId = (): ConnectorId => mintId("cxn");
export const newMemoryId = (): MemoryId => mintId("mem");
export const newRunId = (): RunId => mintId("run");
