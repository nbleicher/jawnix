import { useState } from "react";
import { useNavigate } from "react-router";

import { AppShell } from "../shell/AppShell";
import type { NavigationDestination } from "../shell/Navigation";
import { signOut } from "../auth/customerAuth";
import { Button } from "../../design-system/primitives/Button";

/** Administrator navigation follows domain work, not the record hierarchy. */
const DESTINATIONS: NavigationDestination[] = [
  { to: "/admin/overview", label: "Overview", icon: "◆" },
  { to: "/admin/fulfillment", label: "Fulfillment", icon: "▤" },
  { to: "/admin/acquisition", label: "Acquisition", icon: "▚" },
  { to: "/admin/customers", label: "Customers", icon: "◑" },
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
