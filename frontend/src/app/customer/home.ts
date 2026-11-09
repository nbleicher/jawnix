import { redirect } from "react-router";
import type { LoaderFunctionArgs, ShouldRevalidateFunctionArgs } from "react-router";

import type { BatchIdentity } from "./batchLabel";

/**
 * The Customer's Home, read in one request (`/api/me/overview`).
 *
 * The same read is the Customer shell's access check, so the shell's loader
 * data is what Home renders. Supply stays hidden: nothing here counts
 * inventory or says what is available.
 */

export interface CustomerHomeBatch extends BatchIdentity {
  lead_count: number;
  states: string[];
  submitted_at: string;
  delivered_at: string | null;
  /** The frozen charge, or null when the batch was not billed. */
  cost_cents: number | null;
  /** The batch's page in the portal. */
  href: string;
  file_available: boolean;
  file_expires_at: string | null;
  download_href: string | null;
}

export interface CustomerHomeStep {
  key: "submitted" | "approval" | "preparing" | "ready";
  label: string;
  state: "complete" | "current" | "upcoming";
  occurred_at: string | null;
}

export interface CustomerHomeInProgress {
  batch: CustomerHomeBatch;
  steps: CustomerHomeStep[];
  note: string;
  held_cents: number | null;
  can_cancel: boolean;
}

export type CustomerSetupProblem =
  | "no_appointed_states"
  | "account_pending"
  | "account_inactive";

export interface CustomerHomeAlert {
  kind: "file_expiring" | "low_balance" | "setup_incomplete";
  /** file_expiring: the batch whose file expires soon. */
  batch: CustomerHomeBatch | null;
  /** setup_incomplete: what is missing. */
  problem: CustomerSetupProblem | null;
  /** low_balance: the batch size the balance falls short of, if any. */
  reference_lead_count: number | null;
}

export interface CustomerHomeBilling {
  available_balance_cents: number;
  held_cents: number;
  held_for: BatchIdentity[];
  lead_rate_cents_per_thousand: number | null;
  /** The most leads one batch could buy from the available balance. */
  covers_leads: number | null;
  spent_this_month_cents: number;
}

export interface CustomerHomeData {
  customer_name: string;
  appointed_states: string[];
  can_request: boolean;
  /** Absent for Customers who are not billed. */
  billing: CustomerHomeBilling | null;
  month: {
    starts_at: string;
    delivered_leads: number;
    delivered_batches: number;
  };
  alerts: CustomerHomeAlert[];
  in_progress: CustomerHomeInProgress[];
  recent_batches: CustomerHomeBatch[];
  batch_count: number;
}

const OVERVIEW_PATH = "/api/me/overview";

/** The start of this month on the Customer's own clock. */
export function localMonthStart(now: Date = new Date()): Date {
  return new Date(now.getFullYear(), now.getMonth(), 1);
}

/** The Home read, telling the backend where this month starts locally. */
export function customerHomePath(now: Date = new Date()): string {
  const start = encodeURIComponent(localMonthStart(now).toISOString());
  return `${OVERVIEW_PATH}?month_start=${start}`;
}

function isHome(pathname: string): boolean {
  return /^(?:\/app)?\/home\/?$/.test(pathname);
}

/**
 * Home renders the shell's loader data, so arriving at Home from another page
 * reads it again; moving between the other pages does not.
 */
export function customerShouldRevalidate({
  currentUrl,
  nextUrl,
  defaultShouldRevalidate,
}: ShouldRevalidateFunctionArgs): boolean {
  if (defaultShouldRevalidate) return true;
  return isHome(nextUrl.pathname) && currentUrl.pathname !== nextUrl.pathname;
}

/**
 * Overview became Home. Emailed and bookmarked /app/overview links keep
 * working, query and all; signing in, when needed, happens on the way to Home.
 */
export function overviewRedirectLoader({ request }: LoaderFunctionArgs): Response {
  return redirect(`/home${new URL(request.url).search}`);
}
