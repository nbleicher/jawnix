import { useState } from "react";
import { useLoaderData, useNavigate, useRevalidator } from "react-router";
import type { LoaderFunctionArgs } from "react-router";

import { api } from "../auth/adminMFA";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { ActionLink, Button } from "../../design-system/primitives/Button";
import { ConfirmDialog, Dialog } from "../../design-system/primitives/Dialog";
import { EmptyState } from "../../design-system/primitives/feedback";
import { Field, Input, Select } from "../../design-system/primitives/form";
import {
  Card,
  Cluster,
  DisclosureSection,
  Grid,
  Page,
  Section,
  Stack,
} from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import {
  Heading,
  LabelText,
  Text,
} from "../../design-system/primitives/typography";
import type { CustomerAdminStatus } from "./AdminCustomers";
import { errorMessage, formatAdminDate } from "./AdminCustomers";
import {
  ActivityTimeline,
  loadEntityActivity,
} from "./AdminActivity";
import type { ActivityPage } from "./AdminActivity";
import {
  CustomerLifecycleAction,
  Fact,
  RenameCustomerAction,
  SendPasswordResetAction,
} from "./AdminCustomerActions";
import {
  CustomerAvailabilitySection,
} from "./AdminCustomerAvailability";
import type { CustomerAvailabilityView } from "./AdminCustomerAvailability";
import {
  CustomerBillingSection,
  CustomerCooldownSection,
  CustomerNichePolicySection,
} from "./AdminCustomerControls";
import type {
  CooldownWindowView,
  CustomerBillingView,
  NichePolicyView,
} from "./AdminCustomerControls";

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
  /** Present on the Customer details route; omitted from the agency floating card. */
  billing?: CustomerBillingView;
  cooldown?: CooldownWindowView;
  nichePolicy?: NichePolicyView;
  /** Cached pool availability; omitted when the details payload is reused elsewhere. */
  availability?: CustomerAvailabilityView;
  exclusionLists?: CustomerExclusionListView[];
}

interface CustomerExclusionListView {
  id: string;
  type: string;
  filename: string;
  status: string;
  acceptedRows: number;
  invalidRows: number;
  duplicateRows: number;
  poolImpact: number;
  global: boolean;
  createdAt: string;
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

export async function adminCustomerDetailsLoader({
  params,
}: LoaderFunctionArgs): Promise<CustomerDetailsData> {
  const customerId = params.customerId;
  const [
    details,
    directory,
    activityTimeline,
    billing,
    cooldown,
    nichePolicy,
    availability,
    exclusionLists,
  ] = await Promise.all([
    api<
      Omit<
        CustomerDetailsData,
        | "agencies"
        | "billing"
        | "cooldown"
        | "nichePolicy"
        | "availability"
        | "exclusionLists"
        | "activityTimeline"
      >
    >(`/api/admin/customers/${customerId}/details`),
    api<{
      agencies: { id: number; name: string; active: boolean }[];
    }>("/api/admin/agencies/directory"),
    loadEntityActivity("customer", customerId),
    api<CustomerBillingView>(`/api/admin/customers/${customerId}/billing`),
    api<CooldownWindowView>(
      `/api/admin/customers/${customerId}/cooldown-window`,
    ),
    api<NichePolicyView>(`/api/admin/customers/${customerId}/niche-policy`),
    api<CustomerAvailabilityView>(
      `/api/admin/customers/${customerId}/availability`,
    ),
    api<CustomerExclusionListView[]>(
      `/api/admin/customers/${customerId}/exclusion-lists`,
    ),
  ]);
  return {
    ...details,
    activityTimeline,
    billing,
    cooldown,
    nichePolicy,
    availability,
    exclusionLists,
    agencies: directory.agencies.map(({ id, name, active }) => ({
      id,
      name,
      active,
    })),
  };
}

function CustomerExclusionLists({
  items,
}: {
  items: CustomerExclusionListView[];
}) {
  return (
    <DisclosureSection
      title="Customer Exclusion Lists"
      description="Lists uploaded by this Customer remain scoped to this Customer unless their global effect is separately confirmed in Acquisition."
      summary={`${items.length.toLocaleString()} list${items.length === 1 ? "" : "s"}`}
    >
      {items.length ? (
        <Grid minColumnWidth="16rem">
          {items.map((item) => (
            <Card as="article" key={item.id}>
              <Stack gap={2}>
                <Cluster gap={2} justify="space-between">
                  <Heading level={3} size="sm">
                    {item.filename}
                  </Heading>
                  <StatusBadge
                    tone={item.status === "active" ? "success" : "info"}
                  >
                    {item.status.replaceAll("_", " ")}
                  </StatusBadge>
                </Cluster>
                <Text size="sm">{item.type.replaceAll("_", " ")}</Text>
                <Text size="sm">
                  <strong>{item.acceptedRows.toLocaleString()}</strong> accepted
                  {" · "}
                  {item.poolImpact.toLocaleString()} pool impact
                </Text>
                <Text size="xs" tone="muted">
                  {item.invalidRows.toLocaleString()} invalid ·{" "}
                  {item.duplicateRows.toLocaleString()} duplicates ·{" "}
                  {item.global ? "Global" : "Customer-scoped"}
                </Text>
              </Stack>
            </Card>
          ))}
        </Grid>
      ) : (
        <EmptyState
          title="No Customer Exclusion Lists"
          description="Lists uploaded by this Customer will appear here with ingestion and pool-impact status."
        />
      )}
    </DisclosureSection>
  );
}

function Dependencies({ counts }: { counts: Record<string, number> }) {
  const blocking = Object.entries(counts).filter(([, count]) => count > 0);
  if (!blocking.length) return null;
  return (
    <ul className="admin-customer-details__dependencies">
      {blocking.map(([key, count]) => (
        <li key={key}>
          {dependencyLabel(key)}: {count.toLocaleString()}
        </li>
      ))}
    </ul>
  );
}

function AccountCard({ account }: { account: UserAccountRecord }) {
  return (
    <Card as="article" aria-label="Current User Account">
      <Stack gap={3}>
        <Cluster justify="space-between" align="flex-start">
          <Heading level={3} size="sm">
            {account.email}
          </Heading>
          <StatusBadge tone={account.active ? "success" : "warning"}>
            {account.active ? "Active" : "Inactive"}
          </StatusBadge>
        </Cluster>
        <dl className="admin-customer-details__facts">
          <Fact label="Name">{account.name || "Not provided"}</Fact>
          <Fact label="Created">{formatAdminDate(account.created_at)}</Fact>
        </dl>
      </Stack>
    </Card>
  );
}

export function AdminCustomerDetailsRoute() {
  const data = useLoaderData<CustomerDetailsData>();
  const revalidator = useRevalidator();
  const navigate = useNavigate();
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

  return (
    <Page
      title={customer.name}
      description="The Customer is permanent. Its User Account is only the credential that signs in, and can be replaced without affecting anything below."
      actions={
        <ActionLink href="/app/admin/customers">Back to Customers</ActionLink>
      }
    >
      <Section
        title="Customer"
        description="The durable party. This identity, its Agency membership, and its Licensed States do not change when access is replaced."
      >
        <Card>
          <Stack gap={4}>
            <Cluster justify="space-between" align="flex-start">
              <Heading level={3}>{customer.slug}</Heading>
              <StatusBadge tone={customer.status.tone}>
                {customer.status.label}
              </StatusBadge>
            </Cluster>
            <Text size="sm" tone="muted">
              {customer.status.description}
            </Text>
            <dl className="admin-customer-details__facts">
              <Fact label="Agency">
                {customer.agency_id === null || !customer.agency
                  ? "No Agency"
                  : customer.agency}
              </Fact>
              <Fact label="Licensed States">
                {customer.licensed_states.length
                  ? customer.licensed_states.join(", ")
                  : "None yet"}
              </Fact>
              <Fact label="Last activity">
                {formatAdminDate(customer.last_activity_at)}
              </Fact>
            </dl>
            <div>
              <RenameCustomerAction
                customer={customer}
                onChanged={() => revalidator.revalidate()}
              />
            </div>
          </Stack>
        </Card>
      </Section>

      <Section
        title="Agency assignment"
        description="Current membership can change. Every no-repeat history joined by an assignment remains merged permanently."
      >
        <Card>
          <Stack gap={4} align="flex-start">
            <Text>
              Current Agency:{" "}
              <strong>
                {customer.agency_id === null || !customer.agency
                  ? "No Agency"
                  : customer.agency}
              </strong>
            </Text>
            <Text size="sm" tone="muted">
              Previewing shows the inventory and no-repeat consequence in both
              directions before anything changes.
            </Text>
            <Button variant="primary" onClick={() => open("assignment")}>
              {customer.agency_id === null
                ? "Assign to Agency"
                : "Change Agency assignment"}
            </Button>
          </Stack>
        </Card>
      </Section>

      {data.billing ? (
        <CustomerBillingSection
          billing={data.billing}
          customerId={customer.id}
          onChanged={() => revalidator.revalidate()}
        />
      ) : null}

      {data.cooldown ? (
        <CustomerCooldownSection
          cooldown={data.cooldown}
          customerId={customer.id}
          onChanged={() => revalidator.revalidate()}
        />
      ) : null}

      {data.availability ? (
        <CustomerAvailabilitySection
          availability={data.availability}
          customerId={customer.id}
          onChanged={() => revalidator.revalidate()}
        />
      ) : null}

      {data.nichePolicy ? (
        <CustomerNichePolicySection
          policy={data.nichePolicy}
          customerId={customer.id}
          onChanged={() => revalidator.revalidate()}
        />
      ) : null}

      {data.exclusionLists ? (
        <CustomerExclusionLists items={data.exclusionLists} />
      ) : null}

      <Section
        title="Permanent history"
        description="Everything this Customer has ever been sent. Replacing a User Account never resets any of it."
      >
        <Grid minColumnWidth="10rem">
          <Card>
            <Stack gap={1} align="flex-start">
              <LabelText>Batch Requests</LabelText>
              <Text size="lg" weight="bold">
                {history.requests.toLocaleString()}
              </Text>
            </Stack>
          </Card>
          <Card>
            <Stack gap={1} align="flex-start">
              <LabelText>Distributions</LabelText>
              <Text size="lg" weight="bold">
                {history.distributions.toLocaleString()}
              </Text>
            </Stack>
          </Card>
          <Card>
            <Stack gap={1} align="flex-start">
              <LabelText>Lead Outcomes</LabelText>
              <Text size="lg" weight="bold">
                {history.outcomes.toLocaleString()}
              </Text>
            </Stack>
          </Card>
          <Card>
            <Stack gap={1} align="flex-start">
              <LabelText>Lead Reports</LabelText>
              <Text size="lg" weight="bold">
                {history.reports.toLocaleString()}
              </Text>
            </Stack>
          </Card>
          <Card>
            <Stack gap={1} align="flex-start">
              <LabelText>First delivered</LabelText>
              <Text weight="semibold">
                {formatAdminDate(history.first_delivered_at)}
              </Text>
            </Stack>
          </Card>
          <Card>
            <Stack gap={1} align="flex-start">
              <LabelText>Last delivered</LabelText>
              <Text weight="semibold">
                {formatAdminDate(history.last_delivered_at)}
              </Text>
            </Stack>
          </Card>
        </Grid>
      </Section>

      <Section
        title="User Account"
        description="Replaceable authentication, and nothing more. Administrators provision access by invitation and never set or see a password."
      >
        <Stack gap={4}>
          {data.user_account ? (
            <AccountCard account={data.user_account} />
          ) : (
            <EmptyState
              title="No User Account yet"
              description="Nobody can sign in for this Customer. Invite one — the Customer and its history already exist."
              action={
                <Button variant="primary" onClick={() => open("account")}>
                  Invite User Account
                </Button>
              }
            />
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
                  <StatusBadge tone={data.invitation.status.tone}>
                    {data.invitation.status.label}
                  </StatusBadge>
                </Cluster>
                <Text>
                  {data.invitation.email} was invited on{" "}
                  {formatAdminDate(data.invitation.invited_at)}.
                </Text>
                <Text weight="semibold">
                  {replacing
                    ? "Nothing has been replaced yet. The current User Account stays active until this invitation is accepted."
                    : "No User Account is active until this invitation is accepted."}
                </Text>
                <div>
                  <Button onClick={() => open("cancel")}>
                    Cancel invitation
                  </Button>
                </div>
              </Stack>
            </Card>
          ) : null}
          {data.user_account && !data.invitation ? (
            <Cluster gap={3}>
              <Button variant="primary" onClick={() => open("account")}>
                Replace User Account
              </Button>
              <SendPasswordResetAction
                account={data.user_account}
                onChanged={() => revalidator.revalidate()}
              />
            </Cluster>
          ) : null}
        </Stack>
      </Section>

      <Section
        title="Former User Accounts"
        description="Access that has already been replaced. The Customer, its Agency, and its history above survived every one of them."
      >
        {data.former_accounts.length ? (
          <Stack as="ul" gap={3} className="admin-customer-details__list">
            {data.former_accounts.map((account) => (
              <li key={account.auth_user_id}>
                <Card>
                  <Cluster justify="space-between" align="flex-start">
                    <Text weight="semibold">{account.email}</Text>
                    <Text size="sm" tone="muted">
                      Replaced {formatAdminDate(account.replaced_at)}
                    </Text>
                  </Cluster>
                </Card>
              </li>
            ))}
          </Stack>
        ) : (
          <EmptyState
            title="No former User Accounts"
            description="Access for this Customer has never been replaced."
          />
        )}
      </Section>

      <Section
        title="Activity"
        description="What administrators have done to this Customer and its access."
      >
        <ActivityTimeline
          activity={data.activityTimeline}
          emptyDescription="No administrator has changed this Customer yet."
        />
      </Section>

      <Section
        title="Lifecycle"
        description="These act on the durable Customer, not on its access."
      >
        <Card>
          <Stack gap={4}>
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
              <Text size="sm" tone="warning">
                Deletion and erasure need the Customer deactivated first.
              </Text>
            ) : null}
            {!deletion.can_hard_delete && !deletion.requires_deactivation ? (
              <Stack gap={2} align="flex-start">
                <Text size="sm" tone="warning">
                  Permanent deletion is blocked while these records exist. Only
                  the history-preserving removal is available.
                </Text>
                <Dependencies counts={deletion.dependencies} />
              </Stack>
            ) : null}
            <Cluster gap={3}>
              <CustomerLifecycleAction
                customer={customer}
                onChanged={() => revalidator.revalidate()}
              />
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
        </Card>
      </Section>

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
                <dl className="admin-customer-details__facts">
                  <Fact label="Eligible inventory before">
                    {assignmentPreview.inventory.eligibleBefore.toLocaleString()}
                  </Fact>
                  <Fact label="Eligible inventory after">
                    {assignmentPreview.inventory.eligibleAfter.toLocaleString()}
                  </Fact>
                  <Fact label="Customer history newly blocked for destination">
                    {assignmentPreview.consequences.customerHistoryBlockedForDestination.toLocaleString()}
                  </Fact>
                  <Fact label="Destination history newly blocked for Customer">
                    {assignmentPreview.consequences.destinationHistoryBlockedForCustomer.toLocaleString()}
                  </Fact>
                  <Fact label="Customers in merged history">
                    {assignmentPreview.sharedHistory.customersAfter.toLocaleString()}
                  </Fact>
                  <Fact label="Agencies in merged history">
                    {assignmentPreview.sharedHistory.agenciesAfter.toLocaleString()}
                  </Fact>
                </dl>
                {assignmentPreview.consequences.historyMergeIsPermanent ? (
                  <Text tone="warning" weight="semibold">
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
