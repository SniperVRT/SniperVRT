import pino from "pino";

// Shared pino logger. Child loggers carry task/step ids automatically so the
// audit trail is reconstructable by grepping.
export const rootLogger = pino({
  level: process.env.LOG_LEVEL ?? "info",
  base: { service: "snipervrt" },
  redact: {
    paths: [
      "req.headers.authorization",
      "req.headers.cookie",
      "*.password",
      "*.token",
      "*.apiKey",
      "*.secret",
    ],
    censor: "[REDACTED]",
  },
  timestamp: pino.stdTimeFunctions.isoTime,
});

export type Logger = pino.Logger;

export function loggerFor(bindings: Record<string, unknown>): Logger {
  return rootLogger.child(bindings);
}
