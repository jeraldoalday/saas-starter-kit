import * as Sentry from "@sentry/nextjs";

// The DSN identifies which Sentry project events land in, not a secret key,
// so it's fine to ship under NEXT_PUBLIC_* and reuse for both the browser
// and the server/edge runtimes (see instrumentation-client.ts and
// instrumentation.ts). Leaving it unset - the default for local dev - means
// `Sentry.init` is never called and no Sentry project is required to run.
const SENTRY_ENVIRONMENT =
  process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ?? process.env.NODE_ENV ?? "development";

export function initErrorTracking(): void {
  const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
  if (!dsn) return;

  Sentry.init({
    dsn,
    environment: SENTRY_ENVIRONMENT,
    tracesSampleRate: 0,
  });
}

export function reportError(error: unknown): void {
  Sentry.captureException(error);
}
