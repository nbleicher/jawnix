import { useState } from "react";
import { useLoaderData, useNavigate, useRevalidator } from "react-router";
import type { LoaderFunctionArgs } from "react-router";

import { api } from "../auth/adminMFA";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { ActionLink, Button } from "../../design-system/primitives/Button";
import { ConfirmDialog, Dialog } from "../../design-system/primitives/Dialog";
import { EmptyState } from "../../design-system/primitives/feedback";
import { Field, Input } from "../../design-system/primitives/form";
import {
  Card,
  Cluster,
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
}

type DialogName = "account" | "cancel" | "lifecycle" | "delete" | "erase";
type FailureScope = "account" | "invitation" | "lifecycle";

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
  return api<CustomerDetailsData>(
    `/api/admin/customers/${params.customerId}/details`,
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="admin-customer-details__fact">
      <dt>
        <LabelText>{label}</LabelText>
      </dt>
      <dd>{children}</dd>
    </div>
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
  useDocumentTitle(data.customer.name);

  const { customer, history, deletion } = data;

  function open(name: DialogName) {
    setReason("");
    setConfirmSlug("");
    setHardDelete(false);
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

  function setActive() {
    void run("lifecycle", async () => {
      // PATCH is a full replace: name and agency_id must be resent or the
      // backend would clear the Agency as a side effect of deactivating.
      await api(`/api/admin/customers/${customer.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: customer.name,
          agency_id: customer.agency_id,
          active: !customer.active,
          reason,
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
          </Stack>
        </Card>
      </Section>

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
          ) : data.user_account ? (
            <div>
              <Button variant="primary" onClick={() => open("account")}>
                Replace User Account
              </Button>
            </div>
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
        {data.activity.length ? (
          <Stack as="ul" gap={3} className="admin-customer-details__list">
            {data.activity.map((entry) => (
              <li key={entry.id}>
                <Card>
                  <Stack gap={2}>
                    <Cluster justify="space-between" align="flex-start">
                      <Text weight="semibold">{entry.label}</Text>
                      <Text size="sm" tone="muted">
                        {formatAdminDate(entry.created_at)}
                      </Text>
                    </Cluster>
                    <Text size="sm" tone="muted">
                      {entry.actor}
                    </Text>
                    {entry.reason ? <Text size="sm">{entry.reason}</Text> : null}
                  </Stack>
                </Card>
              </li>
            ))}
          </Stack>
        ) : (
          <EmptyState
            title="No activity recorded"
            description="No administrator has changed this Customer yet."
          />
        )}
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
              <Button onClick={() => open("lifecycle")}>
                {customer.active ? "Deactivate Customer" : "Reactivate Customer"}
              </Button>
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
        open={dialog === "lifecycle"}
        onClose={() => setDialog(null)}
        onConfirm={setActive}
        title={customer.active ? "Deactivate Customer" : "Reactivate Customer"}
        consequence={
          customer.active
            ? "Access stops immediately. Licensed States, Agency membership, and the permanent history are kept."
            : "Access is restored. Nothing in the permanent history changes."
        }
        confirmLabel={customer.active ? "Deactivate" : "Reactivate"}
        destructive={customer.active}
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
