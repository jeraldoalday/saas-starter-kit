"use client";

import { useRouter } from "next/navigation";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/Button";
import type { User } from "@/lib/types";

interface DashboardShellProps {
  user: User;
  onLogout: () => void;
  children: ReactNode;
}

export function DashboardShell({ user, onLogout, children }: DashboardShellProps) {
  const router = useRouter();

  function handleLogout() {
    onLogout();
    router.push("/login");
  }

  return (
    <div className="min-h-screen">
      <header className="flex items-center justify-between border-b border-neutral-800 px-6 py-4">
        <span className="text-sm font-medium text-neutral-300">{user.email}</span>
        <Button variant="secondary" onClick={handleLogout}>
          Log out
        </Button>
      </header>
      <main className="mx-auto max-w-4xl px-6 py-10">{children}</main>
    </div>
  );
}
