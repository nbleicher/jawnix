import { useState } from "react";
import type { ReactNode } from "react";
import {
  Link,
  useLoaderData,
  useNavigate,
  useParams,
  useRevalidator,
} from "react-router";
import type { LoaderFunctionArgs, ShouldRevalidateFunctionArgs } from "react-router";

import { api } from "../auth/adminMFA";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { ActionLink, Button } from "../../design-system/primitives/Button";
import { ConfirmDialog, Dialog } from "../../design-system/primitives/Dialog";
import { FactList } from "../../design-system/primitives/facts";
import type { Fact } from "../../design-system/primitives/facts";
import { Field, Input, Select } from "../../design-system/primitives/form";
import { Kpi, KpiRow } from "../../design-system/primitives/kpi";
import {
  Card,
  Cluster,
  Grid,
  Page,
  Section,
  Stack,
} from "../../design-system/primitives/layout";
import { Panel, RecordColumns } from "../../design-system/primitives/panel";
import { StatusBadge } from "../../design-system/primitives/status";
import { DataTable } from "../../design-system/primitives/table";
import { Tabs } from "../../design-system/primitives/tabs";
import { Heading, Text } from "../../design-system/primitives/typography";
import {
  formatCount,
  formatDatabase,
  formatDateTime,
  formatDay,
  formatRate,
  formatStates,
  plural,
  stageLabel,
} from "../operator/format";
import type {
  CustomerAdminStatus,
  CustomerNeed,
  CustomerOpenBatch,
} from "./AdminCustomers";
import { errorMessage } from "./AdminCustomers";
import {
  ActivityTimeline,
  loadEntityActivity,
} from "./AdminActivity";
import type { ActivityPage } from "./AdminActivity";
import {
  CustomerLifecycleAction,
  RenameCustomerAction,
  SendPasswordResetAction,
} from "./AdminCustomerActions";
import { CustomerAvailabilityPanel } from "./AdminCustomerAvailability";
import type { CustomerAvailabilityView } from "./AdminCustomerAvailability";
import { CustomerBatchesTab, CustomerReportsTab } from "./AdminCustomerBatches";
import type { CustomerLeadReportView } from "./AdminCustomerBatches";
import {
  CustomerBillingSection,
  CustomerCooldownSection,
  CustomerDatabaseSection,
  CustomerExclusionListsSection,
  CustomerNichePolicySection,
  formatUsdCents,
} from "./AdminCustomerControls";
import type {
  CooldownWindowView,
  CustomerBillingView,
  CustomerExclusionListView,
  NichePolicyView,
} from "./AdminCustomerControls";
import { formatAsOf } from "./OperatorNumbers";

import "./AdminCustomerDetails.css";

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

interface AgencyAssignmentPreview {
  customer: {
    id: number;
    name: string;
    agencyId: number | null;
    agency: string;
  };
  destination: {
    id: number;
    name: string;
    active: boolean;
    currentMembers: number;
  } | null;
  inventory: {
    eligibleBefore: number;
    eligibleAfter: number;
  };
  sharedHistory: {
    customersAfter: number;
    agenciesAfter: number;
    distributedLeadsAfter: number;
  };
  consequences: {
    customerHistoryBlockedForDestination: number;
    destinationHistoryBlockedForCustomer: number;
    historyMergeIsPermanent: boolean;
  };
}

type DialogName =
  | "account"
  | "assignment"
  | "cancel"
  | "delete"
  | "erase";
type FailureScope = "account" | "assignment" | "invitation" | "lifecycle";

interface Failure {
  scope: FailureScope;
  message: string;
  dependencies: Record<string, number> | null;
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

function currentTab(value: string | undefined): CustomerTab {
  return CUSTOMER_TABS.some((tab) => tab.key === value) ? (value as CustomerTab) : "summary";
}

export function customerTabHref(customerId: number, tab: CustomerTab): string {
  return tab === "summary"
    ? `/app/admin/customers/${customerId}`
    : `/app/admin/customers/${customerId}/${tab}`;
}

/** The backend counts dependencies with its own storage keys, one of which is
 *  camelCase. Operators read record names, not storage keys. */
const DEPENDENCY_LABELS: Record<string, string> = {
  requests: "Batch Requests",
  distributions: "Distributions",
  outcomes: "Lead Outcomes",
  reports: "Lead Reports",
  dispositions: "Dispositions",
  profiles: "Profiles",
  userAccounts: "User Accounts",
  invitations: "Invitations",
  agencyMemberships: "Agency memberships",
  exclusionLists: "Exclusion Lists",
  creditLedgerEntries: "Credit Ledger entries",
  creditPurchases: "Credit Purchases",
  batchHolds: "Batch Holds",
  accountMigrations: "User Account migration records",
};

function dependencyLabel(key: string): string {
  return DEPENDENCY_LABELS[key] ?? key;
}

/** A blocked hard delete answers 409 with a structured `detail`, which `api`
 *  preserves alongside the flattened message. */
function blockingDependencies(error: unknown): Record<string, number> | null {
  const detail = (error as { detail?: { dependencies?: Record<string, number> } })
    .detail;
  return detail?.dependencies ?? null;
}

function routePath(href: string): string {
  return href.startsWith("/app/") ? href.slice("/app".length) : href;
}

export async function adminCustomerDetailsLoader({
  params,
}: LoaderFunctionArgs): Promise<CustomerDetailsData> {
  const customerId = params.customerId;
  const details = await api<
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
  >(`/api/admin/customers/${customerId}/details`);
  const [
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

function Dependencies({ counts }: { counts: Record<string, number> }) {
  const blocking = Object.entries(counts).filter(([, count]) => count > 0);
  if (!blocking.length) return null;
  return (
    <ul className="admin-customer-details__dependencies">
      {blocking.map(([key, count]) => (
        <li key={key}>
          {dependencyLabel(key)}: {count.toLocaleString("en-US")}
        </li>
      ))}
    </ul>
  );
}

function heldMeta(summary: CustomerSummaryView): string {
  const { balance } = summary;
  if (!balance.billed) return summary.database === "db2" ? "DB2 is not billed" : "Billing off";
  if (!balance.heldCents) return `Nothing held · ${formatRate(balance.rateCentsPerThousand)}`;
  const batches = balance.heldFor.map((number) => `#${number}`).join(", ");
  return `${formatUsdCents(balance.heldCents)} held for ${batches}`;
}

function CustomerNumbers({ summary }: { summary: CustomerSummaryView }) {
  const now = new Date();
  const first = summary.openBatches.first;
  const need = summary.needs[0];
  return (
    <KpiRow label="Customer numbers" columns={6}>
      <Kpi
        label="Balance"
        value={summary.balance.billed ? formatUsdCents(summary.balance.availableCents) : "Not billed"}
        meta={heldMeta(summary)}
      />
      <Kpi
        label="Available now"
        value={summary.available ? formatCount(summary.available.count) : "Not counted"}
        meta={
          summary.available?.asOf
            ? `${formatDatabase(summary.database)} · as of ${formatAsOf(summary.available.asOf, now)}`
            : `${formatDatabase(summary.database)} · refresh on Summary`
        }
      />
      <Kpi
        label="Open batches"
        value={formatCount(summary.openBatches.count)}
        meta={first ? `#${first.number} ${stageLabel(first.stage).toLowerCase()}` : "None open"}
      />
      <Kpi
        label={`Delivered · ${summary.delivered.windowDays} days`}
        value={formatCount(summary.delivered.leads)}
        meta={plural(summary.delivered.batches, "batch", "batches")}
      />
      <Kpi
        label="Lead reports"
        value={formatCount(summary.leadReports.open)}
        meta={
          summary.leadReports.open
            ? `${formatCount(summary.leadReports.open)} open of ${formatCount(summary.leadReports.total)}`
            : summary.leadReports.total
              ? `None open · ${formatCount(summary.leadReports.total)} in total`
              : "None open"
        }
      />
      <Kpi
        label="Needs"
        value={formatCount(summary.needs.length)}
        meta={need ? need.label : "Nothing waiting"}
        alert={summary.needs.length > 0}
      />
    </KpiRow>
  );
}

function actionWords(action: string): string {
  const spaced = action.replaceAll("_", " ").trim();
  return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1) : "Change";
}

/** The latest few changes, so Summary says what happened lately; the
 *  Activity tab has the whole trail. */
function RecentActivity({ activity, customerId }: { activity: ActivityPage; customerId: number }) {
  const entries = activity.entries.slice(0, 3);
  return (
    <Panel title="Recent activity">
      {entries.length ? (
        <ul className="admin-customer-details__recent">
          {entries.map((entry) => (
            <li key={entry.id}>
              <Text size="sm">{actionWords(entry.action)}</Text>
              <Text size="xs" tone="muted">
                {entry.reason ? `${entry.reason} · ` : ""}
                {formatDateTime(entry.recordedAt)}
              </Text>
            </li>
          ))}
        </ul>
      ) : (
        <Text size="sm" tone="muted">
          No administrator has changed this customer yet.
        </Text>
      )}
      <ActionLink href={customerTabHref(customerId, "activity")}>All activity</ActionLink>
    </Panel>
  );
}

function agencyName(customer: CustomerDetailsData["customer"]): string {
  return customer.agency_id === null || !customer.agency ? "No agency" : customer.agency;
}

function describe(data: CustomerDetailsData): string {
  const { customer, summary } = data;
  const parts = [
    agencyName(customer),
    formatStates(customer.licensed_states),
    formatDatabase(summary?.database ?? customer.lead_database),
  ];
  if (summary?.customerSince) parts.push(`customer since ${formatDay(summary.customerSince)}`);
  if (!customer.active) parts.push("deactivated");
  return parts.join(" · ");
}

function NeedsList({ needs }: { needs: CustomerNeed[] }) {
  if (!needs.length) {
    return (
      <Text size="sm" tone="muted">
        Nothing is waiting on this customer.
      </Text>
    );
  }
  return (
    <ul className="admin-customer-details__needs" aria-label="Needs">
      {needs.map((need) => (
        <li key={`${need.kind}-${need.href}-${need.label}`}>
          <Link to={routePath(need.href)}>{need.label}</Link>
        </li>
      ))}
    </ul>
  );
}

export function AdminCustomerDetailsRoute() {
  const data = useLoaderData<CustomerDetailsData>();
  const revalidator = useRevalidator();
  const navigate = useNavigate();
  const { tab: tabParam } = useParams();
  const tab = currentTab(tabParam);
  const [dialog, setDialog] = useState<DialogName | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [reason, setReason] = useState("");
  const [confirmSlug, setConfirmSlug] = useState("");
  const [hardDelete, setHardDelete] = useState(false);
  const [assignmentDestination, setAssignmentDestination] = useState("");
  const [assignmentPreview, setAssignmentPreview] =
    useState<AgencyAssignmentPreview | null>(null);
  const [assignmentConfirmed, setAssignmentConfirmed] = useState(false);
  useDocumentTitle(data.customer.name);

  const { customer, history, deletion } = data;
  const database = data.summary?.database ?? customer.lead_database ?? "db1";
  const refresh = () => revalidator.revalidate();

  function open(name: DialogName) {
    setReason("");
    setConfirmSlug("");
    setHardDelete(false);
    setAssignmentDestination(
      data.customer.agency_id === null
        ? ""
        : String(data.customer.agency_id),
    );
    setAssignmentPreview(null);
    setAssignmentConfirmed(false);
    setFailure(null);
    setDialog(name);
  }

  async function run(scope: FailureScope, task: () => Promise<void>) {
    setBusy(true);
    setFailure(null);
    try {
      await task();
      setDialog(null);
    } catch (caught) {
      // The dialog stays open on failure so the operator can correct the input
      // rather than retype it.
      setFailure({
        scope,
        message: errorMessage(caught),
        dependencies: blockingDependencies(caught),
      });
    } finally {
      setBusy(false);
    }
  }

  function inviteAccount(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const text = (key: string) => String(fields.get(key) ?? "").trim();
    void run("account", async () => {
      await api(`/api/admin/customers/${customer.id}/user-account-invitation`, {
        method: "POST",
        body: JSON.stringify({
          email: text("email"),
          first_name: text("first_name") || undefined,
          last_name: text("last_name") || undefined,
          reason: text("reason") || undefined,
        }),
      });
      await revalidator.revalidate();
    });
  }

  function cancelInvitation() {
    void run("invitation", async () => {
      await api(`/api/admin/customers/${customer.id}/user-account-invitation`, {
        method: "DELETE",
        body: JSON.stringify({ reason }),
      });
      await revalidator.revalidate();
    });
  }

  function previewAssignment() {
    setBusy(true);
    setFailure(null);
    void (async () => {
      try {
        const query = assignmentDestination
          ? `?agency_id=${encodeURIComponent(assignmentDestination)}`
          : "";
        const preview = await api<AgencyAssignmentPreview>(
          `/api/admin/customers/${customer.id}/agency-assignment-preview${query}`,
        );
        setAssignmentPreview(preview);
      } catch (caught) {
        setFailure({
          scope: "assignment",
          message: errorMessage(caught),
          dependencies: null,
        });
      } finally {
        setBusy(false);
      }
    })();
  }

  function assignAgency() {
    void run("assignment", async () => {
      await api(`/api/admin/customers/${customer.id}/agency-assignment`, {
        method: "POST",
        body: JSON.stringify({
          agency_id: assignmentDestination
            ? Number(assignmentDestination)
            : null,
          reason,
          confirmed: assignmentConfirmed,
        }),
      });
      await revalidator.revalidate();
    });
  }

  function deleteCustomer() {
    void run("lifecycle", async () => {
      await api(`/api/admin/customers/${customer.id}`, {
        method: "DELETE",
        body: JSON.stringify({
          confirm_slug: confirmSlug,
          hard_delete: hardDelete,
          reason,
        }),
      });
      await navigate("/admin/customers");
    });
  }

  function erase() {
    void run("lifecycle", async () => {
      await api(`/api/admin/customers/${customer.id}/erase`, {
        method: "POST",
        body: JSON.stringify({ reason }),
      });
      // Erasure retires the Customer to a tombstone, so these details no
      // longer exist to revalidate. Return to the directory instead.
      await navigate("/admin/customers");
    });
  }

  const replacing = data.user_account !== null;
  const availabilityPanel = data.availability ? (
    <CustomerAvailabilityPanel
      availability={data.availability}
      customerId={customer.id}
      database={database}
      onChanged={refresh}
    />
  ) : null;

  const summaryFacts: Fact[] = [
    { term: "Slug", description: customer.slug },
    { term: "Standing", description: customer.status.description },
    {
      term: "Agency",
      description:
        customer.agency_id === null || !customer.agency ? (
          "No agency"
        ) : (
          <Link to={routePath(`/app/admin/agencies/${customer.agency_id}`)}>{customer.agency}</Link>
        ),
    },
    { term: "States", description: customer.licensed_states.length ? customer.licensed_states.join(", ") : "None yet" },
    { term: "Database", description: formatDatabase(database) },
    {
      term: "Last delivery",
      description: data.summary?.lastDeliveryAt
        ? formatDay(data.summary.lastDeliveryAt)
        : history.last_delivered_at
          ? formatDay(history.last_delivered_at)
          : "Never",
    },
  ];

  const historyFacts: Fact[] = [
    { term: "Batch Requests", description: formatCount(history.requests) },
    { term: "Distributions", description: formatCount(history.distributions) },
    { term: "Lead Outcomes", description: formatCount(history.outcomes) },
    { term: "Lead Reports", description: formatCount(history.reports) },
    { term: "First delivered", description: history.first_delivered_at ? formatDay(history.first_delivered_at) : "Never" },
    { term: "Last delivered", description: history.last_delivered_at ? formatDay(history.last_delivered_at) : "Never" },
  ];

  let content: ReactNode;
  switch (tab) {
    case "batches":
      content = <CustomerBatchesTab customerId={customer.id} onChanged={refresh} />;
      break;
    case "billing":
      content = data.billing ? (
        <CustomerBillingSection billing={data.billing} customerId={customer.id} onChanged={refresh} />
      ) : (
        <Text size="sm" tone="muted">Billing could not be read.</Text>
      );
      break;
    case "rules":
      content = (
        <RecordColumns
          main={
            <>
              <CustomerDatabaseSection
                customerId={customer.id}
                database={database}
                billed={data.billing?.billingEnabled ?? data.summary?.balance.billed ?? false}
                onChanged={refresh}
              />
              {data.cooldown ? (
                <CustomerCooldownSection cooldown={data.cooldown} customerId={customer.id} onChanged={refresh} />
              ) : null}
              {data.nichePolicy ? (
                <CustomerNichePolicySection policy={data.nichePolicy} customerId={customer.id} onChanged={refresh} />
              ) : null}
              {data.exclusionLists ? <CustomerExclusionListsSection items={data.exclusionLists} /> : null}
            </>
          }
          aside={availabilityPanel}
        />
      );
      break;
    case "reports":
      content = data.leadReports ? (
        <CustomerReportsTab reports={data.leadReports} />
      ) : (
        <Text size="sm" tone="muted">Lead reports could not be read.</Text>
      );
      break;
    case "access":
      content = (
        <Stack gap={6}>
          <Section
            title="User account"
            description="Replaceable sign-in, and nothing more. Administrators provision access by invitation and never set or see a password."
          >
            <Stack gap={4}>
              {data.user_account ? (
                <Card as="article" aria-label="Current User Account" className="admin-customer-details__account">
                  <Stack gap={3}>
                    <Cluster justify="space-between" align="flex-start">
                      <Heading level={3} size="sm">
                        {data.user_account.email}
                      </Heading>
                      <StatusBadge tone={data.user_account.active ? "success" : "neutral"}>
                        {data.user_account.active ? "Active" : "Inactive"}
                      </StatusBadge>
                    </Cluster>
                    <FactList
                      label="Current User Account"
                      items={[
                        { term: "Name", description: data.user_account.name || "Not provided" },
                        { term: "Created", description: formatDay(data.user_account.created_at) },
                      ]}
                    />
                    {!data.invitation ? (
                      <div>
                        <SendPasswordResetAction account={data.user_account} onChanged={refresh} />
                      </div>
                    ) : null}
                  </Stack>
                </Card>
              ) : (
                <Card>
                  <Stack gap={2}>
                    <Heading level={3} size="sm">
                      No user account yet
                    </Heading>
                    <Text size="sm" tone="muted">
                      Nobody can sign in for this customer. The customer and its history already exist; invite a user account from the top of this page.
                    </Text>
                  </Stack>
                </Card>
              )}

              {data.invitation ? (
                <Card
                  as="article"
                  className="admin-customer-details__pending"
                  aria-label="Pending invitation"
                >
                  <Stack gap={3}>
                    <Cluster justify="space-between" align="flex-start">
                      <Heading level={3} size="sm">
                        Invitation pending
                      </Heading>
                      <StatusBadge tone="info">{data.invitation.status.label}</StatusBadge>
                    </Cluster>
                    <Text>
                      {data.invitation.email} was invited on {formatDay(data.invitation.invited_at)}.
                    </Text>
                    <Text weight="semibold">
                      {replacing
                        ? "Nothing has been replaced yet. The current User Account stays active until this invitation is accepted."
                        : "No User Account is active until this invitation is accepted."}
                    </Text>
                    <div>
                      <Button onClick={() => open("cancel")}>Cancel invitation</Button>
                    </div>
                  </Stack>
                </Card>
              ) : null}
            </Stack>
          </Section>

          <Section
            title="Former user accounts"
            description="Access that has already been replaced. The customer, its agency, and its history survived every one of them."
          >
            <DataTable
              label="Former user accounts"
              columns={[
                { key: "email", header: "Email", cell: (account) => account.email },
                { key: "name", header: "Name", cell: (account) => account.name || "—" },
                { key: "created", header: "Created", cell: (account) => formatDay(account.created_at) },
                {
                  key: "replaced",
                  header: "Replaced",
                  cell: (account) => (account.replaced_at ? formatDay(account.replaced_at) : "—"),
                },
              ]}
              rows={data.former_accounts}
              rowKey={(account) => account.auth_user_id}
              empty="Access for this customer has never been replaced."
            />
          </Section>
        </Stack>
      );
      break;
    case "activity":
      content = (
        <Section
          title="Activity"
          description="What administrators have done to this customer and its access."
        >
          <ActivityTimeline
            activity={data.activityTimeline}
            emptyDescription="No administrator has changed this Customer yet."
          />
        </Section>
      );
      break;
    default:
      content = (
        <RecordColumns
          main={
            <>
              <Section title="Needs">
                <NeedsList needs={data.summary?.needs ?? []} />
              </Section>
              <Section
                title="Customer"
                description="The durable party. This identity, its agency membership, and its states do not change when access is replaced."
              >
                <FactList label="Customer" items={summaryFacts} />
              </Section>
              <Section
                title="Permanent history"
                description="Everything this Customer has ever been sent. Replacing a User Account never resets any of it."
              >
                <FactList label="Permanent history" items={historyFacts} />
              </Section>
              <Section
                title="Lifecycle"
                description="These act on the durable customer, not on its access."
              >
                <Stack gap={3}>
                  {deletion.tombstoned ? (
                    <Text>
                      Personal data for this Customer has been erased. The record
                      remains as a tombstone so the permanent history above stays
                      intact.
                    </Text>
                  ) : null}
                  <Text size="sm" tone="muted">
                    {customer.active
                      ? "Deactivating stops all access and new work. Every record above is kept."
                      : "This Customer is deactivated. Reactivating restores access and new work."}
                  </Text>
                  {deletion.requires_deactivation ? (
                    <Text size="sm" tone="muted">
                      Deletion and erasure need the Customer deactivated first.
                    </Text>
                  ) : null}
                  {!deletion.can_hard_delete && !deletion.requires_deactivation ? (
                    <Stack gap={2} align="flex-start">
                      <Text size="sm" tone="muted">
                        Permanent deletion is blocked while these records exist. Only
                        the history-preserving removal is available.
                      </Text>
                      <Dependencies counts={deletion.dependencies} />
                    </Stack>
                  ) : null}
                  <Cluster gap={3}>
                    <CustomerLifecycleAction customer={customer} onChanged={refresh} />
                    <Button
                      variant="danger"
                      onClick={() => open("delete")}
                      disabled={deletion.requires_deactivation}
                    >
                      Delete Customer
                    </Button>
                    <Button
                      variant="danger"
                      onClick={() => open("erase")}
                      disabled={deletion.requires_deactivation || deletion.tombstoned}
                    >
                      Erase personal data
                    </Button>
                  </Cluster>
                </Stack>
              </Section>
            </>
          }
          aside={
            <>
              {availabilityPanel}
              <Panel title="Agency">
                <FactList
                  label="Agency"
                  items={[{ term: "Member of", description: agencyName(customer) }]}
                />
                <Text size="xs" tone="muted">
                  Membership can change. No-repeat history joined by an assignment stays merged permanently.
                </Text>
                <div>
                  <Button onClick={() => open("assignment")}>
                    {customer.agency_id === null ? "Assign to Agency" : "Change Agency assignment"}
                  </Button>
                </div>
              </Panel>
              <Panel title="Access">
                <FactList
                  label="Access"
                  items={[
                    {
                      term: "User account",
                      description: data.user_account?.email ?? "None",
                    },
                    {
                      term: "Invitation",
                      description: data.invitation ? data.invitation.email : "None pending",
                    },
                  ]}
                />
                <ActionLink href={customerTabHref(customer.id, "access")}>Manage access</ActionLink>
              </Panel>
              <RecentActivity activity={data.activityTimeline} customerId={customer.id} />
            </>
          }
        />
      );
  }

  return (
    <Page
      title={customer.name}
      width="data"
      density="data"
      description={describe(data)}
      actions={
        <>
          <StatusBadge tone={customer.active ? "success" : "neutral"}>
            {customer.active ? "Active" : "Deactivated"}
          </StatusBadge>
          <RenameCustomerAction customer={customer} onChanged={refresh} label="Rename" />
          {data.invitation ? null : (
            <Button onClick={() => open("account")}>
              {replacing ? "Replace user account" : "Invite user account"}
            </Button>
          )}
        </>
      }
    >
      {data.unavailableSections?.length ? (
        <Card>
          <Stack gap={3} align="flex-start">
            <Text role="alert" tone="danger" size="sm">
              These sections could not be loaded and are missing from this
              page: {data.unavailableSections.join(", ")}. Everything else is
              current.
            </Text>
            <Button onClick={() => revalidator.revalidate()}>
              Retry loading
            </Button>
          </Stack>
        </Card>
      ) : null}

      {data.summary ? <CustomerNumbers summary={data.summary} /> : null}

      <Tabs
        label="Customer sections"
        current={tab}
        items={CUSTOMER_TABS.map((item) => ({
          key: item.key,
          label: item.label,
          href: customerTabHref(customer.id, item.key),
        }))}
      />

      {content}

      <Dialog
        open={dialog === "assignment"}
        onClose={() => setDialog(null)}
        title="Change Agency assignment"
        description="Preview the permanent inventory consequence, then provide an audit reason and confirm the merge explicitly."
        dismissOnBackdrop={false}
      >
        <Stack gap={4}>
          {failure?.scope === "assignment" ? (
            <Text role="alert" tone="danger" size="sm">
              {failure.message}
            </Text>
          ) : null}
          <Field label="Destination Agency">
            <Select
              value={assignmentDestination}
              onChange={(event) => {
                setAssignmentDestination(event.target.value);
                setAssignmentPreview(null);
                setAssignmentConfirmed(false);
              }}
            >
              <option value="">No Agency</option>
              {data.agencies.map((agency) => (
                <option
                  key={agency.id}
                  value={String(agency.id)}
                  disabled={!agency.active}
                >
                  {agency.active ? agency.name : `${agency.name} (deactivated)`}
                </option>
              ))}
            </Select>
          </Field>
          <div>
            <Button onClick={previewAssignment} busy={busy}>
              Preview consequences
            </Button>
          </div>
          {assignmentPreview ? (
            <Card className="admin-customer-details__assignment-preview">
              <Stack gap={4}>
                <Heading level={3} size="sm">
                  Permanent consequence
                </Heading>
                <Text weight="semibold">
                  {assignmentPreview.destination
                    ? `Moving to ${assignmentPreview.destination.name}`
                    : "Removing current membership"}
                </Text>
                <FactList
                  label="Permanent consequence"
                  items={[
                    {
                      term: "Eligible inventory before",
                      description: formatCount(assignmentPreview.inventory.eligibleBefore),
                    },
                    {
                      term: "Eligible inventory after",
                      description: formatCount(assignmentPreview.inventory.eligibleAfter),
                    },
                    {
                      term: "Customer history newly blocked for destination",
                      description: formatCount(
                        assignmentPreview.consequences.customerHistoryBlockedForDestination,
                      ),
                    },
                    {
                      term: "Destination history newly blocked for Customer",
                      description: formatCount(
                        assignmentPreview.consequences.destinationHistoryBlockedForCustomer,
                      ),
                    },
                    {
                      term: "Customers in merged history",
                      description: formatCount(assignmentPreview.sharedHistory.customersAfter),
                    },
                    {
                      term: "Agencies in merged history",
                      description: formatCount(assignmentPreview.sharedHistory.agenciesAfter),
                    },
                  ]}
                />
                {assignmentPreview.consequences.historyMergeIsPermanent ? (
                  <Text weight="semibold">
                    This history merge is permanent. Moving the Customer again
                    will not split it.
                  </Text>
                ) : (
                  <Text size="sm" tone="muted">
                    Removing current membership does not split history that was
                    already merged.
                  </Text>
                )}
                <Field label="Reason" required>
                  <Input
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    autoComplete="off"
                  />
                </Field>
                <label className="admin-customer-details__toggle">
                  <input
                    type="checkbox"
                    checked={assignmentConfirmed}
                    onChange={(event) =>
                      setAssignmentConfirmed(event.target.checked)
                    }
                  />
                  <span>
                    I understand that shared no-repeat history never splits.
                  </span>
                </label>
                <Cluster gap={3}>
                  <Button
                    variant="primary"
                    onClick={assignAgency}
                    busy={busy}
                    disabled={!assignmentConfirmed || !reason.trim()}
                  >
                    Confirm assignment
                  </Button>
                  <Button onClick={() => setDialog(null)}>Cancel</Button>
                </Cluster>
              </Stack>
            </Card>
          ) : null}
        </Stack>
      </Dialog>

      <Dialog
        open={dialog === "account"}
        onClose={() => setDialog(null)}
        title={replacing ? "Replace User Account" : "Invite User Account"}
        description="An invitation is emailed to this address. Administrators never set or see a password."
      >
        <form onSubmit={inviteAccount}>
          <Stack gap={4}>
            {failure?.scope === "account" ? (
              <Text role="alert" tone="danger" size="sm">
                {failure.message}
              </Text>
            ) : null}
            <Text size="sm">
              {replacing
                ? "The current User Account stays active until this invitation is accepted. The Customer, its Agency, its Licensed States, and its permanent history are untouched."
                : "The Customer and its permanent history already exist. This only creates the credential that signs in."}
            </Text>
            <Field label="Email" required>
              <Input name="email" type="email" autoComplete="off" autoFocus />
            </Field>
            <Grid minColumnWidth="12rem" gap={4}>
              <Field label="First name">
                <Input name="first_name" autoComplete="off" />
              </Field>
              <Field label="Last name">
                <Input name="last_name" autoComplete="off" />
              </Field>
            </Grid>
            <Field
              label="Reason"
              description="Recorded on this Customer's activity trail."
            >
              <Input name="reason" autoComplete="off" />
            </Field>
            <Cluster gap={3}>
              <Button
                type="submit"
                variant="primary"
                busy={busy}
                busyLabel="Sending…"
              >
                Send invitation
              </Button>
              <Button onClick={() => setDialog(null)}>Cancel</Button>
            </Cluster>
          </Stack>
        </form>
      </Dialog>

      <ConfirmDialog
        open={dialog === "cancel"}
        onClose={() => setDialog(null)}
        onConfirm={cancelInvitation}
        title="Cancel invitation"
        consequence={
          replacing
            ? "The invited address can no longer be used. The current User Account was never replaced and stays active."
            : "The invited address can no longer be used. This Customer will have no way to sign in."
        }
        confirmLabel="Cancel invitation"
        cancelLabel="Keep invitation"
        busy={busy}
      >
        <Stack gap={3}>
          {failure?.scope === "invitation" ? (
            <Text role="alert" tone="danger" size="sm">
              {failure.message}
            </Text>
          ) : null}
          <Field label="Reason" required>
            <Input
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              autoComplete="off"
            />
          </Field>
        </Stack>
      </ConfirmDialog>

      <ConfirmDialog
        open={dialog === "delete"}
        onClose={() => setDialog(null)}
        onConfirm={deleteCustomer}
        title="Delete Customer"
        consequence={
          deletion.can_hard_delete
            ? "This removes the Customer. Permanent deletion cannot be undone."
            : "This removes the Customer from administration while its permanent history is preserved."
        }
        confirmLabel="Delete Customer"
        busy={busy}
      >
        <Stack gap={3}>
          {failure?.scope === "lifecycle" ? (
            <Stack gap={2} align="flex-start">
              <Text role="alert" tone="danger" size="sm">
                {failure.message}
              </Text>
              {failure.dependencies ? (
                <Dependencies counts={failure.dependencies} />
              ) : null}
            </Stack>
          ) : null}
          {deletion.can_hard_delete ? (
            <label className="admin-customer-details__toggle">
              <input
                type="checkbox"
                checked={hardDelete}
                onChange={(event) => setHardDelete(event.target.checked)}
              />
              <span>Permanently delete every record for this Customer</span>
            </label>
          ) : (
            <Stack gap={2} align="flex-start">
              <Text size="sm">
                Permanent deletion is unavailable while these records exist. The
                Customer will be removed with its history preserved.
              </Text>
              <Dependencies counts={deletion.dependencies} />
            </Stack>
          )}
          <Field
            label={`Type ${customer.slug} to confirm`}
            description="Deleting the wrong Customer is not recoverable."
            required
          >
            <Input
              value={confirmSlug}
              onChange={(event) => setConfirmSlug(event.target.value)}
              autoComplete="off"
            />
          </Field>
          <Field label="Reason" required>
            <Input
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              autoComplete="off"
            />
          </Field>
        </Stack>
      </ConfirmDialog>

      <ConfirmDialog
        open={dialog === "erase"}
        onClose={() => setDialog(null)}
        onConfirm={erase}
        title="Erase personal data"
        consequence="Personal details are erased and the Customer becomes a tombstone. The permanent history stays intact and is never reset."
        confirmLabel="Erase personal data"
        busy={busy}
      >
        <Stack gap={3}>
          {failure?.scope === "lifecycle" ? (
            <Text role="alert" tone="danger" size="sm">
              {failure.message}
            </Text>
          ) : null}
          <Field label="Reason" required>
            <Input
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              autoComplete="off"
            />
          </Field>
        </Stack>
      </ConfirmDialog>
    </Page>
  );
}
