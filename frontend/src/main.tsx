import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "@/App";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { startAnalytics } from "@/lib/analytics";
import { registerTokenRefresh } from "@/lib/api-client";
import { refreshAccessToken } from "@/lib/auth";
import { initErrorTracking } from "@/lib/error-tracking";
import "@/styles/globals.css";

// Runs before render so a crash during the first paint is still reported.
initErrorTracking();

// So a request that outlives the access token retries with a new one instead
// of ending the session — see api-client's registerTokenRefresh for why this
// is wired up here rather than imported directly where it's used.
registerTokenRefresh(refreshAccessToken);

// Buffered events would otherwise be lost when the tab goes away.
startAnalytics();

const container = document.getElementById("root");

if (!container) {
  throw new Error('Root element "#root" not found in index.html');
}

createRoot(container).render(
  <StrictMode>
    <ErrorBoundary>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ErrorBoundary>
  </StrictMode>
);
