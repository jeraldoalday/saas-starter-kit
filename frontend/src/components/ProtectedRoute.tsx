import { Navigate, Outlet } from "react-router-dom";

import { DashboardShell } from "@/components/DashboardShell";
import { useAuth } from "@/hooks/useAuth";
import type { User } from "@/lib/types";

/** Shape handed down to routes nested under {@link ProtectedRoute}. */
export interface ProtectedOutletContext {
  user: User;
}

/**
 * Gates its nested routes behind an authenticated session. The App Router
 * equivalent was a layout with a redirect effect; React Router expresses the
 * same thing declaratively with `<Navigate>`, so there is no flash of the
 * protected UI before the redirect happens.
 */
export function ProtectedRoute() {
  const { user, loading, logout } = useAuth();

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-neutral-500">
        Loading…
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  return (
    <DashboardShell user={user} onLogout={logout}>
      <Outlet context={{ user } satisfies ProtectedOutletContext} />
    </DashboardShell>
  );
}
