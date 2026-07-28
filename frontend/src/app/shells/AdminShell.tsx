import { AppShell } from "../shell/AppShell";
import type { NavigationDestination } from "../shell/Navigation";

/** Administrator navigation follows domain work, not the record hierarchy. */
const DESTINATIONS: NavigationDestination[] = [
  { to: "/admin/overview", label: "Overview", icon: "◆" },
  { to: "/admin/fulfillment", label: "Fulfillment", icon: "▤" },
  { to: "/admin/acquisition", label: "Acquisition", icon: "▚" },
  { to: "/admin/customers", label: "Customers", icon: "◑" },
];

export function AdminShell() {
  return <AppShell audience="Administration" destinations={DESTINATIONS} />;
}
