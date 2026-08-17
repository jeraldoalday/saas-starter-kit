"use client";

import { useEffect } from "react";

import { reportError } from "@/lib/error-tracking";

export default function GlobalError({
  error,
}: {
  error: Error & { digest?: string };
}) {
  useEffect(() => {
    reportError(error);
  }, [error]);

  return (
    <html lang="en">
      <body>
        <h2>Something went wrong.</h2>
      </body>
    </html>
  );
}
