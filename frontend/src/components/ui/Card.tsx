import type { HTMLAttributes } from "react";

export function Card({ className = "", ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={`rounded-lg border border-neutral-800 bg-neutral-900/60 p-6 ${className}`}
      {...props}
    />
  );
}
