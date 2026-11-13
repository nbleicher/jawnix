import type { LoaderFunctionArgs, ShouldRevalidateFunctionArgs } from "react-router";

import { api } from "../api";
import type {
  CustomerAdminStatus,
  CustomerNeed,
  CustomerOpenBatch,
} from "./AdminCustomers";
import { loadEntityActivity } from "./AdminActivity";
import type { ActivityPage } from "./AdminActivity";
import type { CustomerAvailabilityView } from "./AdminCustomerAvailability";
import type { CustomerLeadReportView } from "./AdminCustomerBatches";
import type {
  CooldownWindowView,
  CustomerBillingView,
  CustomerExclusionListView,
  NichePolicyView,
} from "./AdminCustomerControls";

/**
 * The Customer record page's data: the details payload and the sections read
 * beside it, the tabs the page is split into, and the loader that reads them
 * together.
 */

export interface UserAccountRecord {
  auth_user_id: string;
  email: string;
  name: string;
  active: boolean;
  created_at: string;
  replaced_at: string | null;
  replaced_by_auth_user_id: string | null;
}

/** The Customer page's numbers row and Needs list
 *  (GET /api/admin/customers/{id}/summary), in the Customers table's terms. */
export interface CustomerSummaryView {
  customerId: number;
  database: string;
  states: string[];
  customerSince: string | null;
  needs: CustomerNeed[];
  balance: {
    billed: boolean;
    availableCents: number;
    heldCents: number;
    heldFor: number[];
    rateCentsPerThousand: number | null;
  };
  available: { count: number; asOf: string | null } | null;
  openBatches: { count: number; first: CustomerOpenBatch | null };
  delivered: { windowDays: number; leads: number; batches: number };
  lastDeliveryAt: string | null;
  leadReports: { open: number; total: number };
}

export interface CustomerDetailsData {
  activityTimeline: ActivityPage;
  customer: {
    id: number;
    slug: string;
    name: string;
    agency_id: number | null;
    agency: string;
    active: boolean;
    licensed_states: string[];
    status: CustomerAdminStatus;
    last_activity_at: string | null;
    lead_database?: "db1" | "db2";
  };
  history: {
    requests: number;
    distributions: number;
    outcomes: number;
    reports: number;
    first_delivered_at: string | null;
    last_delivered_at: string | null;
  };
  user_account: UserAccountRecord | null;
  invitation: {
    id: string;
    email: string;
    invited_at: string;
    replaces_auth_user_id: string | null;
    status: CustomerAdminStatus;
  } | null;
  former_accounts: UserAccountRecord[];
  activity: {
    id: string;
    action: string;
    label: string;
    actor: string;
    reason: string;
    created_at: string;
  }[];
  deletion: {
    dependencies: Record<string, number>;
    requires_deactivation: boolean;
    can_hard_delete: boolean;
    tombstoned: boolean;
  };
  agencies: {
    id: number;
    name: string;
    active: boolean;
  }[];
  /** Present on the Customer page; omitted from the agency floating card. */
  summary?: CustomerSummaryView;
  billing?: CustomerBillingView;
  cooldown?: CooldownWindowView;
  nichePolicy?: NichePolicyView;
  /** Cached pool availability; omitted when the details payload is reused elsewhere. */
  availability?: CustomerAvailabilityView;
  exclusionLists?: CustomerExclusionListView[];
  leadReports?: CustomerLeadReportView[];
  /** Sections whose own fetch failed, so the page renders without them.
   *  An absent section and a broken one look identical otherwise. */
  unavailableSections?: string[];
}

/** The record's sub-sections (V7). Each is its own address. */
export const CUSTOMER_TABS = [
  { key: "summary", label: "Summary" },
  { key: "batches", label: "Batches" },
  { key: "billing", label: "Billing" },
  { key: "rules", label: "Rules" },
  { key: "reports", label: "Reports" },
  { key: "access", label: "Access" },
  { key: "activity", label: "Activity" },
] as const;

type CustomerTab = (typeof CUSTOMER_TABS)[number]["key"];

export function currentTab(value: string | undefined): CustomerTab {
  return CUSTOMER_TABS.some((tab) => tab.key === value) ? (value as CustomerTab) : "summary";
}

export function customerTabHref(customerId: number, tab: CustomerTab): string {
  return tab === "summary"
    ? `/app/admin/customers/${customerId}`
    : `/app/admin/customers/${customerId}/${tab}`;
}

/** A blocked hard delete answers 409 with a structured `detail`, which `api`
 *  preserves alongside the flattened message. */
export function blockingDependencies(error: unknown): Record<string, number> | null {
  const detail = (error as { detail?: { dependencies?: Record<string, number> } })
    .detail;
  return detail?.dependencies ?? null;
}

export async function adminCustomerDetailsLoader({
  params,
}: LoaderFunctionArgs): Promise<CustomerDetailsData> {
  const customerId = params.customerId;
  // Every section is independent of the details payload, so all ten reads go
  // out together: one round trip instead of details first and the rest after.
  const [
    details,
    summary,
    directory,
    activityTimeline,
    billing,
    cooldown,
    nichePolicy,
    availability,
    exclusionLists,
    leadReports,
  ] = await Promise.all([
    api<
      Omit<
        CustomerDetailsData,
        | "agencies"
        | "summary"
        | "billing"
        | "cooldown"
        | "nichePolicy"
        | "availability"
        | "exclusionLists"
        | "leadReports"
        | "activityTimeline"
        | "unavailableSections"
      >
    >(`/api/admin/customers/${customerId}/details`),
    api<CustomerSummaryView>(`/api/admin/customers/${customerId}/summary`).catch(
      () => undefined,
    ),
    api<{
      agencies: { id: number; name: string; active: boolean }[];
    }>("/api/admin/agencies/directory").catch(() => undefined),
    loadEntityActivity("customer", customerId).catch(() => undefined),
    api<CustomerBillingView>(
      `/api/admin/customers/${customerId}/billing`,
    ).catch(() => undefined),
    api<CooldownWindowView>(
      `/api/admin/customers/${customerId}/cooldown-window`,
    ).catch(() => undefined),
    api<NichePolicyView>(
      `/api/admin/customers/${customerId}/niche-policy`,
    ).catch(() => undefined),
    api<CustomerAvailabilityView>(
      `/api/admin/customers/${customerId}/availability`,
    ).catch(() => undefined),
    api<CustomerExclusionListView[]>(
      `/api/admin/customers/${customerId}/exclusion-lists`,
    ).catch(() => undefined),
    api<CustomerLeadReportView[]>(
      `/api/admin/customers/${customerId}/lead-reports`,
    ).catch(() => undefined),
  ]);
  // A section that failed to load is not a section that does not exist, and
  // an administrator making a decision needs to know which one they are
  // looking at. Naming the gap keeps the page usable without letting a
  // transient error read as "this Customer has no Billing".
  const unavailableSections = [
    summary === undefined ? "Numbers" : null,
    billing === undefined ? "Billing" : null,
    cooldown === undefined ? "Cooldown window" : null,
    availability === undefined ? "Availability" : null,
    nichePolicy === undefined ? "Niche policy" : null,
    exclusionLists === undefined ? "Exclusion lists" : null,
    leadReports === undefined ? "Lead reports" : null,
    activityTimeline === undefined ? "Activity" : null,
    directory === undefined ? "Agency directory" : null,
  ].filter((label): label is string => label !== null);
  return {
    ...details,
    activityTimeline: activityTimeline ?? {
      entries: [],
      page: 1,
      pageSize: 25,
      total: 0,
      pages: 1,
    },
    ...(summary ? { summary } : {}),
    ...(billing ? { billing } : {}),
    ...(cooldown ? { cooldown } : {}),
    ...(nichePolicy ? { nichePolicy } : {}),
    ...(availability ? { availability } : {}),
    ...(exclusionLists ? { exclusionLists } : {}),
    ...(leadReports ? { leadReports } : {}),
    ...(unavailableSections.length ? { unavailableSections } : {}),
    agencies: (directory?.agencies ?? []).map(({ id, name, active }) => ({
      id,
      name,
      active,
    })),
  };
}

/**
 * Switching tabs or opening a batch beside the list is not new data: the
 * page keeps what it read. Actions re-read it explicitly, and another
 * Customer reads its own.
 */
export function adminCustomerDetailsShouldRevalidate({
  currentUrl,
  nextUrl,
  currentParams,
  nextParams,
  defaultShouldRevalidate,
}: ShouldRevalidateFunctionArgs): boolean {
  if (currentParams.customerId !== nextParams.customerId) return defaultShouldRevalidate;
  if (currentUrl.href === nextUrl.href) return defaultShouldRevalidate;
  return false;
}
