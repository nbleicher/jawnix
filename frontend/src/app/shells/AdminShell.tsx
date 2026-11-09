import { useState } from "react";
import { useNavigate } from "react-router";

import { AppShell } from "../shell/AppShell";
import type { NavigationDestination } from "../shell/Navigation";
import { signOut } from "../auth/customerAuth";
import { Button } from "../../design-system/primitives/Button";
import { GlobalSearch } from "../operator/GlobalSearch";
import "./AdminShell.css";

function within(pathname: string, path: string): boolean {
  return pathname === path || pathname.startsWith(`${path}/`);
}

/**
 * Operator navigation, split by how often each tool is used: Today for the
 * daily work, records next, occasional tools last (V1, V8).
 *
 * Insights and Setup point at the existing Source Performance and Acquisition
 * pages until their own pages exist. Inventory owns lead lookup and its tools
 * under /admin/inventory.
 */
const DESTINATIONS: NavigationDestination[] = [
  { to: "/admin/today", label: "Today", icon: "◆" },
  { to: "/admin/customers", label: "Customers", icon: "◑" },
  { to: "/admin/inventory", label: "Inventory", icon: "▦" },
  { to: "/admin/acquisition/performance", label: "Insights", icon: "▲" },
  {
    to: "/admin/acquisition",
    label: "Setup",
    icon: "▚",
    // Setup owns Acquisition and the occasional tools, but not the Source
    // Performance page that Insights points at.
    match: (pathname) =>
      (within(pathname, "/admin/acquisition") &&
        !within(pathname, "/admin/acquisition/performance")) ||
      within(pathname, "/admin/agencies") ||
      within(pathname, "/admin/security"),
  },
  { to: "/admin/activity", label: "Activity", icon: "◷" },
];

export function AdminShell() {
  const navigate = useNavigate();
  const [signingOut, setSigningOut] = useState(false);

  async function handleSignOut() {
    if (signingOut) return;
    setSigningOut(true);
    try {
      await signOut();
    } finally {
      navigate("/sign-in", { replace: true });
    }
  }

  return (
    <AppShell
      audience="Administration"
      className="jx-shell--operator"
      destinations={DESTINATIONS}
      headerActions={
        <>
          <GlobalSearch />
          <Button
            variant="ghost"
            busy={signingOut}
            busyLabel="Signing out…"
            onClick={handleSignOut}
          >
            Sign out
          </Button>
        </>
      }
    />
  );
}
