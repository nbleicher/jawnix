import { useState } from "react";
import { useNavigate } from "react-router";

import { AppShell } from "../shell/AppShell";
import type { NavigationDestination } from "../shell/Navigation";
import { signOut } from "../auth/customerAuth";
import { Button } from "../../design-system/primitives/Button";
import { useRouteTheme } from "../../design-system/theme/ThemeProvider";

/** Administrator navigation follows domain work, not the record hierarchy. */
const DESTINATIONS: NavigationDestination[] = [
  { to: "/admin/overview", label: "Overview", icon: "◆" },
  { to: "/admin/fulfillment", label: "Fulfillment", icon: "▤" },
  { to: "/admin/acquisition", label: "Acquisition", icon: "▚" },
  { to: "/admin/customers", label: "Customers", icon: "◑" },
  { to: "/admin/activity", label: "Activity", icon: "◷" },
];

export function AdminShell() {
  // Administration shares the Customer portal's Opaline product language.
  // Privileged Scraper screens keep their operational frame, but no longer
  // replace the application theme with a separate terminal palette.
  useRouteTheme("opaline");
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
      destinations={DESTINATIONS}
      headerActions={
        <Button
          variant="ghost"
          busy={signingOut}
          busyLabel="Signing out…"
          onClick={handleSignOut}
        >
          Sign out
        </Button>
      }
    />
  );
}
