import { useState } from "react";
import { useNavigate } from "react-router";

import { ActionLink, Button } from "../../design-system/primitives/Button";
import { signOut } from "../auth/customerAuth";

export const SUPPORT_HREF = "mailto:noah@jawnix.com";

/**
 * Support and Sign out. The desktop header carries them; on phones the header
 * keeps only the balance and Buy credits (V8), so Account carries them there.
 */
export function CustomerSessionActions() {
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
    <>
      <ActionLink href={SUPPORT_HREF} variant="ghost">
        Support
      </ActionLink>
      <Button
        variant="ghost"
        busy={signingOut}
        busyLabel="Signing out…"
        onClick={() => void handleSignOut()}
      >
        Sign out
      </Button>
    </>
  );
}
