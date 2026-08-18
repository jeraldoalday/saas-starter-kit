import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "@/App";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { startAnalytics } from "@/lib/analytics";
import { initErrorTracking } from "@/lib/error-tracking";
import "@/styles/globals.css";

// Runs before render so a crash during the first paint is still reported.
initErrorTracking();

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
