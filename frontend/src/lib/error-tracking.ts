import * as Sentry from "@sentry/react";

// The DSN identifies which Sentry project events land in, not a secret key, so
// it's fine to ship in the client bundle under VITE_*. Leaving it unset - the
// default for local dev - means `Sentry.init` is never called and no Sentry
// project is required to run.
export function initErrorTracking(): void {
  const dsn = import.meta.env.VITE_SENTRY_DSN;
  if (!dsn) return;

  Sentry.init({
    dsn,
    environment: import.meta.env.VITE_SENTRY_ENVIRONMENT ?? import.meta.env.MODE,
    tracesSampleRate: 0,
  });
}

export function reportError(error: unknown): void {
  Sentry.captureException(error);
}
