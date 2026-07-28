import { useLocation } from "react-router";

import { AppShell } from "../shell/AppShell";
import type { NavigationDestination } from "../shell/Navigation";
import { useRouteTheme } from "../../design-system/theme/ThemeProvider";

/** Administrator navigation follows domain work, not the record hierarchy. */
const DESTINATIONS: NavigationDestination[] = [
  { to: "/admin/overview", label: "Overview", icon: "◆" },
  { to: "/admin/fulfillment", label: "Fulfillment", icon: "▤" },
  { to: "/admin/acquisition", label: "Acquisition", icon: "▚" },
  { to: "/admin/customers", label: "Customers", icon: "◑" },
];

export function AdminShell() {
  const location = useLocation();

  // Acquisition is the GMS/OPS workspace and carries the terminal theme; the
  // rest of administration stays in the product language. #62 replaces this
  // pathname check when the real workspace lands.
  const inAcquisition = location.pathname.startsWith("/admin/acquisition");
  useRouteTheme(inAcquisition ? "terminal" : "jawnix");

  return <AppShell audience="Administration" destinations={DESTINATIONS} />;
}
