import { AppShell } from "../shell/AppShell";
import type { NavigationDestination } from "../shell/Navigation";
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

  return <AppShell audience="Customer" destinations={DESTINATIONS} />;
}
