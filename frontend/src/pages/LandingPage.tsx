import { Link } from "react-router-dom";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";

const features = [
  {
    title: "Auth built in",
    description: "JWT access/refresh tokens with bcrypt password hashing, ready to extend.",
  },
  {
    title: "Billing wired up",
    description: "Stripe checkout and webhook handling for subscription state.",
  },
  {
    title: "Tested & CI'd",
    description: "Backend and frontend test suites run on every push via GitHub Actions.",
  },
];

export function LandingPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-4xl flex-col items-center justify-center gap-12 px-6 py-24 text-center">
      <div className="flex flex-col gap-4">
        <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
          Ship your SaaS, not your boilerplate
        </h1>
        <p className="text-lg text-neutral-400">
          A FastAPI + React starter kit with auth, billing, and CI already wired up.
        </p>
      </div>

      <div className="flex gap-3">
        <Link to="/register">
          <Button>Get started</Button>
        </Link>
        <Link to="/login">
          <Button variant="secondary">Log in</Button>
        </Link>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        {features.map((feature) => (
          <Card key={feature.title} className="text-left">
            <h2 className="mb-2 text-sm font-semibold text-neutral-100">{feature.title}</h2>
            <p className="text-sm text-neutral-400">{feature.description}</p>
          </Card>
        ))}
      </div>
    </main>
  );
}
