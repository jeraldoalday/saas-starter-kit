"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { apiRequest } from "@/lib/api-client";
import { getAccessToken } from "@/lib/auth";
import { useAuth } from "@/hooks/useAuth";

export default function DashboardPage() {
  const { user } = useAuth();
  const [starting, setStarting] = useState(false);

  async function handleSubscribe() {
    setStarting(true);
    try {
      const { checkout_url } = await apiRequest<{ checkout_url: string }>(
        "/billing/checkout",
        { method: "POST", token: getAccessToken() }
      );
      window.location.href = checkout_url;
    } finally {
      setStarting(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold text-neutral-100">Dashboard</h1>

      <Card>
        <h2 className="mb-2 text-sm font-semibold text-neutral-100">Account</h2>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm text-neutral-400">
          <dt>Email</dt>
          <dd>{user?.email}</dd>
          <dt>Verified</dt>
          <dd>{user?.is_verified ? "Yes" : "No"}</dd>
        </dl>
      </Card>

      <Card>
        <h2 className="mb-2 text-sm font-semibold text-neutral-100">Subscription</h2>
        <p className="mb-4 text-sm text-neutral-400">
          No active subscription. Start a checkout session to subscribe.
        </p>
        <Button onClick={handleSubscribe} disabled={starting}>
          {starting ? "Redirecting…" : "Subscribe"}
        </Button>
      </Card>
    </div>
  );
}
