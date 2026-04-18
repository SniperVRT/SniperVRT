import { describe, it, expect } from "vitest";
import {
  TASK_TRANSITIONS,
  STEP_TRANSITIONS,
  assertTaskTransition,
  assertStepTransition,
  isTerminalTask,
  isTerminalStep,
} from "../../packages/orchestrator/src/state-machine.js";

describe("task state machine", () => {
  it("allows every declared transition", () => {
    for (const [from, tos] of Object.entries(TASK_TRANSITIONS)) {
      for (const to of tos) {
        expect(() => assertTaskTransition(from as never, to)).not.toThrow();
      }
    }
  });

  it("rejects undeclared transitions", () => {
    expect(() => assertTaskTransition("pending", "succeeded")).toThrow(
      /illegal task transition/,
    );
    expect(() => assertTaskTransition("succeeded", "running")).toThrow();
    expect(() => assertTaskTransition("failed", "running")).toThrow();
  });

  it("treats same-state as a no-op", () => {
    expect(() => assertTaskTransition("running", "running")).not.toThrow();
  });

  it("flags terminal states", () => {
    expect(isTerminalTask("succeeded")).toBe(true);
    expect(isTerminalTask("failed")).toBe(true);
    expect(isTerminalTask("cancelled")).toBe(true);
    expect(isTerminalTask("running")).toBe(false);
    expect(isTerminalTask("awaiting_approval")).toBe(false);
  });

  it("terminal states have no outgoing edges", () => {
    expect(TASK_TRANSITIONS.succeeded).toEqual([]);
    expect(TASK_TRANSITIONS.failed).toEqual([]);
    expect(TASK_TRANSITIONS.cancelled).toEqual([]);
  });
});

describe("step state machine", () => {
  it("allows every declared transition", () => {
    for (const [from, tos] of Object.entries(STEP_TRANSITIONS)) {
      for (const to of tos) {
        expect(() => assertStepTransition(from as never, to)).not.toThrow();
      }
    }
  });

  it("rejects illegal step transitions", () => {
    expect(() => assertStepTransition("pending", "succeeded")).toThrow();
    expect(() => assertStepTransition("succeeded", "running")).toThrow();
  });

  it("treats skipped and cancelled as terminal", () => {
    expect(isTerminalStep("skipped")).toBe(true);
    expect(isTerminalStep("cancelled")).toBe(true);
    expect(isTerminalStep("succeeded")).toBe(true);
    expect(isTerminalStep("failed")).toBe(true);
    expect(isTerminalStep("running")).toBe(false);
    expect(isTerminalStep("awaiting_validation")).toBe(false);
  });
});
