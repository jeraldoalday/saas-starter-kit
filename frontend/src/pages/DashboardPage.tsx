import { useEffect } from "react";
import { useOutletContext } from "react-router-dom";

import { BillingCard } from "@/components/BillingCard";
import type { ProtectedOutletContext } from "@/components/ProtectedRoute";
import { Card } from "@/components/ui/Card";
import { UsageCard } from "@/components/UsageCard";
import { trackEvent } from "@/lib/analytics";

export function DashboardPage() {
  const { user } = useOutletContext<ProtectedOutletContext>();

  useEffect(() => {
    trackEvent("dashboard.viewed", { verified: user.is_verified });
  }, [user.is_verified]);

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold text-neutral-100">Dashboard</h1>

      <Card>
        <h2 className="mb-2 text-sm font-semibold text-neutral-100">Account</h2>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm text-neutral-400">
          <dt>Email</dt>
          <dd>{user.email}</dd>
          <dt>Verified</dt>
          <dd>{user.is_verified ? "Yes" : "No"}</dd>
        </dl>
      </Card>

      <BillingCard />

      <UsageCard />
    </div>
  );
}
