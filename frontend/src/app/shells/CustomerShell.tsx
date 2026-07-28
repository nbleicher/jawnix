import { useState } from "react";
import { useNavigate } from "react-router";

import { AppShell } from "../shell/AppShell";
import type { NavigationDestination } from "../shell/Navigation";
import { signOutCustomer } from "../auth/customerAuth";
import { Button } from "../../design-system/primitives/Button";
import { useRouteTheme } from "../../design-system/theme/ThemeProvider";

/** Customer-facing navigation, matching the Customer's jobs rather than the
 *  backend's structure. */
const DESTINATIONS: NavigationDestination[] = [
  { to: "/overview", label: "Overview", icon: "◆" },
  { to: "/requests", label: "Requests", icon: "▤" },
  { to: "/feedback", label: "Feedback", icon: "✎" },
  { to: "/account", label: "Account", icon: "◑" },
];

export function CustomerShell() {
  // The Customer portal is always the product language.
  useRouteTheme("jawnix");
  const navigate = useNavigate();
  const [signingOut, setSigningOut] = useState(false);

  async function signOut() {
    if (signingOut) return;
    setSigningOut(true);
    try {
      await signOutCustomer();
    } finally {
      navigate("/sign-in", { replace: true });
    }
  }

  return (
    <AppShell
      audience="Customer"
      destinations={DESTINATIONS}
      headerActions={
        <Button
          variant="ghost"
          busy={signingOut}
          busyLabel="Signing out…"
          onClick={signOut}
        >
          Sign out
        </Button>
      }
    />
  );
}
