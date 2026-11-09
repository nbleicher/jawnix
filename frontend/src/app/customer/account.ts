/**
 * Account's tabs and their addresses (V7: tabs live in the URL). Profile is
 * Account itself; links elsewhere in the portal open the tab they mean.
 */
export type AccountTab = "profile" | "states" | "billing" | "exclusion-lists" | "password";

export const ACCOUNT_TABS: { key: AccountTab; label: string }[] = [
  { key: "profile", label: "Profile" },
  { key: "states", label: "Appointed states" },
  { key: "billing", label: "Billing" },
  { key: "exclusion-lists", label: "Exclusion lists" },
  { key: "password", label: "Password" },
];

/** The router path of one tab: "/account", "/account/states", … */
export function accountTabPath(tab: AccountTab): string {
  return tab === "profile" ? "/account" : `/account/${tab}`;
}

/** Where "Add" or "Edit appointed states" goes from anywhere in the portal. */
export const APPOINTED_STATES_HREF = `/app${accountTabPath("states")}`;
