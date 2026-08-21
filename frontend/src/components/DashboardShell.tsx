import type { ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/Button";
import type { User } from "@/lib/types";

interface DashboardShellProps {
  user: User;
  onLogout: () => void;
  children: ReactNode;
}

/** Routes reachable from the header, in order. */
const NAV_ITEMS = [
  // `end` so /dashboard stops matching once a nested route is active;
  // without it the Overview link would stay highlighted on every child page.
  { to: "/dashboard", label: "Overview", end: true },
  { to: "/dashboard/api-keys", label: "API keys", end: false },
];

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
