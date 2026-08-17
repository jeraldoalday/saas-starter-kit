import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "@/App";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { initErrorTracking } from "@/lib/error-tracking";
import "@/styles/globals.css";

// Runs before render so a crash during the first paint is still reported.
initErrorTracking();

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
