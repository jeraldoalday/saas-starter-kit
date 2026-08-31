/**
 * The dashboard's sections, in the order the header shows them.
 *
 * Its own module rather than a second export from DashboardShell: a file
 * that exports both a component and a constant breaks React Fast Refresh,
 * and the tests that enumerate this list need it without pulling a
 * component in behind it.
 */
export interface NavItem {
  to: string;
  label: string;
  /**
   * Stops a parent path matching once a nested route is active. Without it
   * on "/dashboard" the Overview link stays highlighted on every child page.
   */
  end: boolean;
}

export const NAV_ITEMS: readonly NavItem[] = [
  { to: "/dashboard", label: "Overview", end: true },
  { to: "/dashboard/api-keys", label: "API keys", end: false },
  { to: "/dashboard/sessions", label: "Sessions", end: false },
  { to: "/dashboard/webhooks", label: "Webhooks", end: false },
  { to: "/dashboard/security", label: "Security", end: false },
];
