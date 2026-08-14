import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "SaaS Starter",
  description: "A production-lean SaaS starter kit.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
