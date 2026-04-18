export type BrowserActionKind =
  | "read"
  | "click"
  | "type"
  | "navigate"
  | "extract"
  | "wait"
  | "done"
  | "abort";

export interface BrowserAction {
  kind: BrowserActionKind;
  targetSelector?: string | null;
  text?: string | null;
  url?: string | null;
  extractFields?: string[];
  requiresApproval?: boolean;
  reason: string;
  confidence: number;
}

export interface BrowserGuidedStep extends BrowserAction {
  expected?: string; // optional selector expected after action
}

export interface PageDigest {
  url: string;
  title: string;
  // Short list of actionable elements, one per line.
  actionables: string;
  // Truncated text for context.
  textDigest: string;
}
