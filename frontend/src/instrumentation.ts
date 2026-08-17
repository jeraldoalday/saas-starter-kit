import * as Sentry from "@sentry/nextjs";

import { initErrorTracking } from "@/lib/error-tracking";

export async function register(): Promise<void> {
  initErrorTracking();
}

export const onRequestError = Sentry.captureRequestError;
