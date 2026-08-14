"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import type { ReactNode } from "react";

import { DashboardShell } from "@/components/DashboardShell";
import { useAuth } from "@/hooks/useAuth";

export default function DashboardLayout({ children }: { children: ReactNode }) {
  const { user, loading, logout } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) {
      router.replace("/login");
    }
  }, [loading, user, router]);

  if (loading || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-neutral-500">
        Loading…
      </div>
    );
  }

  return (
    <DashboardShell user={user} onLogout={logout}>
      {children}
    </DashboardShell>
  );
}
