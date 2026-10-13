import { useState } from "react";
import { useNavigate } from "react-router";
import { AppShell } from "../shell/AppShell";
import { signOut } from "../auth/customerAuth";
import { Button } from "../../design-system/primitives/Button";

const DESTINATIONS = [{ to:"/manager/uploads", label:"Upload leads" }, { to:"/manager/customers", label:"Users" }];

export function ManagedAdminShell() {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  async function leave() {
    if (busy) return;
    setBusy(true);
    try { await signOut(); }
    finally { navigate("/sign-in", { replace:true }); }
  }
  return <AppShell audience="Managed administration" destinations={DESTINATIONS}
    headerActions={<Button variant="ghost" busy={busy} busyLabel="Signing out…" onClick={leave}>Sign out</Button>} />;
}
