import { useState } from "react";
import { useNavigate } from "react-router";

import { AppShell } from "../shell/AppShell";
import type { NavigationDestination } from "../shell/Navigation";
import { signOut } from "../auth/customerAuth";
import {
  ActionLink,
  Button,
} from "../../design-system/primitives/Button";
import { useRouteTheme } from "../../design-system/theme/ThemeProvider";
import { CreditWalletProvider } from "../billing/CreditWalletContext";
import { CreditWalletWidget } from "../billing/CreditWalletWidget";

/** Customer-facing navigation, matching the Customer's jobs rather than the
 *  backend's structure. */
const DESTINATIONS: NavigationDestination[] = [
  { to: "/overview", label: "Overview", icon: "◆" },
  { to: "/requests", label: "Requests", icon: "▤" },
  { to: "/feedback", label: "Feedback", icon: "✎" },
  { to: "/account", label: "Account", icon: "◑" },
];

export function CustomerShell() {
  // The Customer portal is always the Opaline product language.
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
    <CreditWalletProvider>
      <AppShell
        audience="Customer"
        destinations={DESTINATIONS}
        headerActions={
          <>
            <CreditWalletWidget />
            <ActionLink href="mailto:hai@jawnix.com" variant="ghost">
              Support
            </ActionLink>
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
    </CreditWalletProvider>
  );
}
