import { Component, type ReactNode } from "react";

import { reportError } from "@/lib/error-tracking";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
}

/**
 * Catches render errors that escape the route tree and reports them.
 *
 * This takes over from the App Router's `global-error.tsx` convention. A plain
 * SPA has no framework-level error slot, so the boundary is mounted explicitly
 * around the router in main.tsx. React only supports error boundaries as class
 * components, which is why this one is not a hook.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error): void {
    reportError(error);
  }

  render(): ReactNode {
    if (!this.state.hasError) {
      return this.props.children;
    }

    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-2 px-6 text-center">
        <h2 className="text-lg font-semibold text-neutral-100">Something went wrong.</h2>
        <p className="text-sm text-neutral-400">
          The error has been reported. Try reloading the page.
        </p>
      </div>
    );
  }
}
