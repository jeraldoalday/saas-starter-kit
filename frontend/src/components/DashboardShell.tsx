import type { ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/Button";
import { NAV_ITEMS } from "@/lib/navigation";
import type { User } from "@/lib/types";

interface DashboardShellProps {
  user: User;
  onLogout: () => void;
  children: ReactNode;
}

export function DashboardShell({ user, onLogout, children }: DashboardShellProps) {
  const navigate = useNavigate();

  function handleLogout() {
    onLogout();
    navigate("/login");
  }

  return (
    <div className="min-h-screen">
      <header className="flex items-center justify-between border-b border-neutral-800 px-6 py-4">
        <nav className="flex items-center gap-4" aria-label="Main">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `text-sm transition-colors ${
                  isActive ? "text-neutral-100" : "text-neutral-500 hover:text-neutral-300"
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="flex items-center gap-4">
          <span className="text-sm font-medium text-neutral-300">{user.email}</span>
          <Button variant="secondary" onClick={handleLogout}>
            Log out
          </Button>
        </div>
      </header>
      <main className="mx-auto max-w-4xl px-6 py-10">{children}</main>
    </div>
  );
}
