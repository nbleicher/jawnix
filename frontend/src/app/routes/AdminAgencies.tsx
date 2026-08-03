import { useState } from "react";
import {
  Form,
  Link,
  useLoaderData,
  useNavigate,
  useRevalidator,
  useSearchParams,
} from "react-router";
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
import { CustomerFloatingCard, Fact } from "./AdminCustomerActions";
import type { CustomerDetailsData } from "./AdminCustomerDetails";

import "./AdminAgencies.css";

interface SharedHistory {
  customers: number;
  agencies: number;
  distributedLeads: number;
}

export interface AgencyMemberRow {
  id: number;
  slug: string;
  name: string;
  active: boolean;
  licensedStates: string[];
  href: string;
}

interface AgencyDirectoryRow {
  id: number;
  slug: string;
  name: string;
  active: boolean;
  status: CustomerAdminStatus;
  members: AgencyMemberRow[];
  currentMembers: number;
  sharedHistory: SharedHistory;
  lastActivityAt: string | null;
  href: string;
}

export interface AgencyDirectoryData {
  filters: {
    query: string;
    status: "all" | "active" | "deactivated";
  };
  total: number;
  matched: number;
  agencies: AgencyDirectoryRow[];
  independent: AgencyMemberRow[];
  openCustomer: CustomerDetailsData | null;
}

export interface AgencyDetailsData {
  activityTimeline: ActivityPage;
  agency: {
    id: number;
    slug: string;
    name: string;
    active: boolean;
    status: CustomerAdminStatus;
  };
  members: {
    id: number;
    slug: string;
    name: string;
    active: boolean;
    licensedStates: string[];
    href: string;
  }[];
  sharedHistory: SharedHistory;
  membershipHistory: {
    id: number;
    customerId: number;
    customer: string;
    startedAt: string;
    endedAt: string | null;
    assignedBy: string;
    reason: string;
  }[];
  activity: {
    id: string;
    action: string;
    label: string;
    actor: string;
    reason: string;
    createdAt: string;
  }[];
  deletion: {
    dependencies: Record<string, number>;
    requiresDeactivation: boolean;
    canHardDelete: boolean;
  };
}

function routePath(href: string): string {
  return href.startsWith("/app/") ? href.slice("/app".length) : href;
}

export async function adminAgencyDirectoryLoader({
  request,
}: LoaderFunctionArgs): Promise<AgencyDirectoryData> {
  const requested = new URL(request.url).searchParams;
  const query = new URLSearchParams();
  for (const key of ["q", "status"]) {
    const value = requested.get(key);
    if (value) query.set(key, value);
  }
  const search = query.toString();
  const customerId = requested.get("customer");
  const [directory, openCustomer] = await Promise.all([
    api<Omit<AgencyDirectoryData, "openCustomer">>(
      `/api/admin/agencies/directory${search ? `?${search}` : ""}`,
    ),
    customerId
      ? api<CustomerDetailsData>(
          `/api/admin/customers/${encodeURIComponent(customerId)}/details`,
        ).catch((caught: unknown) => {
          // Removed or forbidden Customers get the in-page "unavailable"
          // dialog; anything else is a real failure for the error boundary.
          const status = (caught as { status?: number }).status;
          if (status === 403 || status === 404) return null;
          throw caught;
        })
      : Promise.resolve(null),
  ]);
  return { ...directory, openCustomer };
}

export async function adminAgencyDetailsLoader({
  params,
}: LoaderFunctionArgs): Promise<AgencyDetailsData> {
  const [details, activityTimeline] = await Promise.all([
    api<Omit<AgencyDetailsData, "activityTimeline">>(
      `/api/admin/agencies/${params.agencyId}/details`,
    ),
    loadEntityActivity("agency", params.agencyId),
  ]);
  return { ...details, activityTimeline };
}

function AgencyCard({ row }: { row: AgencyDirectoryRow }) {
  return (
    <Card as="article" aria-label={row.name}>
      <Stack gap={4}>
        <Cluster justify="space-between" align="flex-start">
          <Stack gap={1}>
            <Heading level={3}>
              <Link className="admin-agencies__name" to={routePath(row.href)}>
                {row.name}
              </Link>
            </Heading>
            <Text size="sm" tone="muted">
              {row.slug}
            </Text>
          </Stack>
          <StatusBadge tone={row.status.tone}>
            {row.status.label}
          </StatusBadge>
        </Cluster>
        <dl className="admin-agencies__facts">
          <Fact label="Current members">
            {row.currentMembers.toLocaleString()}
          </Fact>
          <Fact label="Permanent Customers">
            {row.sharedHistory.customers.toLocaleString()}
          </Fact>
          <Fact label="Shared-history Leads">
            {row.sharedHistory.distributedLeads.toLocaleString()}
          </Fact>
          <Fact label="Last activity">
            {formatAdminDate(row.lastActivityAt)}
          </Fact>
        </dl>
        <MemberList members={row.members} agencyActive={row.active} />
      </Stack>
    </Card>
  );
}

function MemberList({
  members,
  agencyActive,
  emptyMessage = "No Customers in this Agency.",
}: {
  members: AgencyMemberRow[];
  agencyActive: boolean;
  emptyMessage?: string;
}) {
  const [searchParams] = useSearchParams();
  if (!members.length) {
    return <Text tone="muted">{emptyMessage}</Text>;
  }
  return (
    <Stack as="ul" gap={2} className="admin-agencies__list">
      {members.map((member) => {
        const customerParams = new URLSearchParams(searchParams);
        customerParams.set("customer", String(member.id));
        const active = member.active && agencyActive;
        return (
          <li key={member.id}>
            <Link
              className="admin-agencies__member"
              to={`?${customerParams.toString()}`}
            >
              <Stack gap={1}>
                <Text weight="semibold">{member.name}</Text>
                <Text size="sm" tone="muted">
                  {member.slug}
                  {member.licensedStates.length
                    ? ` · ${member.licensedStates.join(", ")}`
                    : ""}
                </Text>
              </Stack>
              <StatusBadge tone={active ? "success" : "warning"}>
                {active ? "Active" : "Inactive"}
              </StatusBadge>
            </Link>
          </li>
        );
      })}
    </Stack>
  );
}

function CreateAgencyDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    setBusy(true);
    setFailure("");
    try {
      const created = await api<{ agencyId: number }>("/api/admin/agencies", {
        method: "POST",
        body: JSON.stringify({
          name: String(fields.get("name") ?? "").trim(),
          reason: String(fields.get("reason") ?? "").trim(),
        }),
      });
      await navigate(`/admin/agencies/${created.agencyId}`);
    } catch (caught) {
      setFailure(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Create Agency"
      description="An Agency is an internal grouping for permanent no-repeat history. It receives no login or Customer dashboard."
    >
      <form onSubmit={(event) => void submit(event)}>
        <Stack gap={4}>
          {failure ? (
            <Text role="alert" tone="danger" size="sm">
              {failure}
            </Text>
          ) : null}
          <Field label="Agency name" required>
            <Input name="name" autoComplete="off" autoFocus />
          </Field>
          <Field label="Reason" description="Recorded in Agency activity." required>
            <Input name="reason" autoComplete="off" />
          </Field>
          <Cluster gap={3}>
            <Button type="submit" variant="primary" busy={busy}>
              Create Agency
            </Button>
            <Button onClick={onClose}>Cancel</Button>
          </Cluster>
        </Stack>
      </form>
    </Dialog>
  );
}

export function AdminAgenciesRoute() {
  const data = useLoaderData<AgencyDirectoryData>();
  const revalidator = useRevalidator();
  const [searchParams, setSearchParams] = useSearchParams();
  const [createOpen, setCreateOpen] = useState(false);
  const openId = searchParams.get("customer");
  const closeCustomer = () => {
    const next = new URLSearchParams(searchParams);
    next.delete("customer");
    setSearchParams(next);
  };
  useDocumentTitle("Agencies");
  return (
    <Page
      title="Agencies"
      description="Internal servicing groups whose no-repeat histories merge permanently with assigned Customers. Agencies never sign in and never receive a cross-Customer dashboard."
      actions={
        <Cluster gap={3}>
          <ActionLink href="/app/admin/customers">View Customers</ActionLink>
          <Button variant="primary" onClick={() => setCreateOpen(true)}>
            Create Agency
          </Button>
        </Cluster>
      }
    >
      <Section
        title="Find an Agency"
        description="Search by Agency name or slug, including deactivated groups whose history is retained."
      >
        <Card>
          <Form method="get" role="search">
            <Grid minColumnWidth="14rem" gap={4}>
              <Field label="Search">
                <Input
                  name="q"
                  type="search"
                  defaultValue={data.filters.query}
                />
              </Field>
              <Field label="Status">
                <Select name="status" defaultValue={data.filters.status}>
                  <option value="all">All</option>
                  <option value="active">Active</option>
                  <option value="deactivated">Deactivated</option>
                </Select>
              </Field>
              <Stack style={{ justifyContent: "flex-end" }}>
                <Button type="submit" variant="primary">
                  Search
                </Button>
              </Stack>
            </Grid>
          </Form>
        </Card>
      </Section>
      <Section
        title="Agency directory"
        description="Shared-history counts include every Customer and Agency permanently joined by an assignment."
      >
        <Stack gap={4}>
          <Text size="sm" tone="muted">
            Showing {data.matched.toLocaleString()} of{" "}
            {data.total.toLocaleString()} Agencies.
          </Text>
          {data.agencies.length ? (
            <Stack gap={4}>
              {data.agencies.map((agency) => (
                <AgencyCard key={agency.id} row={agency} />
              ))}
            </Stack>
          ) : (
            <EmptyState
              title="No Agencies match this search"
              description="Widen the search, or create a new internal Agency."
            />
          )}
          <Card as="article" aria-label="Independent Customers">
            <Stack gap={4}>
              <Heading level={3}>Independent Customers</Heading>
              <MemberList
                members={data.independent}
                agencyActive
                emptyMessage="No Independent Customers."
              />
            </Stack>
          </Card>
        </Stack>
      </Section>
      <CreateAgencyDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
      />
      {openId && data.openCustomer ? (
        <CustomerFloatingCard
          details={data.openCustomer}
          onClose={closeCustomer}
          onChanged={() => revalidator.revalidate()}
        />
      ) : openId ? (
        <Dialog
          open
          onClose={closeCustomer}
          title="Customer unavailable"
          description="This Customer could not be loaded. It may have been removed or you may no longer have access."
        />
      ) : null}
    </Page>
  );
}

type DetailsDialog = "edit" | "lifecycle" | "delete";

const DEPENDENCY_LABELS: Record<string, string> = {
  customers: "Current Customers",
  distributions: "Distribution Events",
  membershipHistory: "Membership history",
};

export function AdminAgencyDetailsRoute() {
  const data = useLoaderData<AgencyDetailsData>();
  const revalidator = useRevalidator();
  const navigate = useNavigate();
  const [dialog, setDialog] = useState<DetailsDialog | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const [reason, setReason] = useState("");
  const [confirmSlug, setConfirmSlug] = useState("");
  const [hardDelete, setHardDelete] = useState(false);
  useDocumentTitle(data.agency.name);

  function open(name: DetailsDialog) {
    setDialog(name);
    setFailure("");
    setReason("");
    setConfirmSlug("");
    setHardDelete(false);
  }

  async function update(name: string, active: boolean, auditReason: string) {
    setBusy(true);
    setFailure("");
    try {
      await api(`/api/admin/agencies/${data.agency.id}`, {
        method: "PATCH",
        body: JSON.stringify({ name, active, reason: auditReason }),
      });
      setDialog(null);
      await revalidator.revalidate();
    } catch (caught) {
      setFailure(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  function edit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    void update(
      String(fields.get("name") ?? "").trim(),
      data.agency.active,
      String(fields.get("reason") ?? "").trim(),
    );
  }

  async function remove() {
    setBusy(true);
    setFailure("");
    try {
      await api(`/api/admin/agencies/${data.agency.id}`, {
        method: "DELETE",
        body: JSON.stringify({
          confirm_slug: confirmSlug,
          hard_delete: hardDelete,
          reason,
        }),
      });
      await navigate("/admin/agencies");
    } catch (caught) {
      setFailure(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const dependencies = Object.entries(data.deletion.dependencies).filter(
    ([, count]) => count > 0,
  );

  return (
    <Page
      title={data.agency.name}
      description="An internal grouping for allocation history. This record has no login and exposes no cross-Customer dashboard."
      actions={
        <ActionLink href="/app/admin/agencies">Back to Agencies</ActionLink>
      }
    >
      <Section title="Agency" description={data.agency.status.description}>
        <Card>
          <Stack gap={4}>
            <Cluster justify="space-between" align="flex-start">
              <Stack gap={1}>
                <Heading level={3}>{data.agency.slug}</Heading>
                <Text size="sm" tone="muted">
                  Internal grouping · no User Account
                </Text>
              </Stack>
              <StatusBadge tone={data.agency.status.tone}>
                {data.agency.status.label}
              </StatusBadge>
            </Cluster>
            <Cluster gap={3}>
              <Button onClick={() => open("edit")}>Edit name</Button>
              <Button onClick={() => open("lifecycle")}>
                {data.agency.active ? "Deactivate Agency" : "Reactivate Agency"}
              </Button>
            </Cluster>
          </Stack>
        </Card>
      </Section>

      <Section
        title="Shared-history impact"
        description="These counts never shrink when a Customer moves away. Future allocation respects the whole merged history in both directions."
      >
        <Grid minColumnWidth="12rem">
          <Card>
            <Stack gap={1}>
              <LabelText>Permanent Customers</LabelText>
              <Text size="lg" weight="bold">
                {data.sharedHistory.customers.toLocaleString()}
              </Text>
            </Stack>
          </Card>
          <Card>
            <Stack gap={1}>
              <LabelText>Merged Agencies</LabelText>
              <Text size="lg" weight="bold">
                {data.sharedHistory.agencies.toLocaleString()}
              </Text>
            </Stack>
          </Card>
          <Card>
            <Stack gap={1}>
              <LabelText>Blocked historical Leads</LabelText>
              <Text size="lg" weight="bold">
                {data.sharedHistory.distributedLeads.toLocaleString()}
              </Text>
            </Stack>
          </Card>
        </Grid>
      </Section>

      <Section
        title="Current members"
        description="Only current membership changes. Every prior no-repeat relationship remains permanent."
      >
        {data.members.length ? (
          <Grid minColumnWidth="18rem">
            {data.members.map((member) => (
              <Card as="article" aria-label={member.name} key={member.id}>
                <Stack gap={2}>
                  <Heading level={3} size="sm">
                    <Link to={routePath(member.href)}>{member.name}</Link>
                  </Heading>
                  <Text size="sm" tone="muted">
                    {member.licensedStates.length
                      ? member.licensedStates.join(", ")
                      : "No Licensed States"}
                  </Text>
                  <StatusBadge tone={member.active ? "success" : "warning"}>
                    {member.active ? "Active" : "Deactivated"}
                  </StatusBadge>
                </Stack>
              </Card>
            ))}
          </Grid>
        ) : (
          <EmptyState
            title="No current members"
            description="Customers may still share permanent history with this Agency after moving away."
          />
        )}
      </Section>

      <Section
        title="Membership history"
        description="The assignment periods retained for audit and deletion safety."
      >
        {data.membershipHistory.length ? (
          <Stack as="ul" gap={3} className="admin-agencies__list">
            {data.membershipHistory.map((membership) => (
              <li key={membership.id}>
                <Card>
                  <Stack gap={2}>
                    <Cluster justify="space-between" align="flex-start">
                      <Text weight="semibold">{membership.customer}</Text>
                      <Text size="sm" tone="muted">
                        {membership.endedAt
                          ? `Ended ${formatAdminDate(membership.endedAt)}`
                          : "Current"}
                      </Text>
                    </Cluster>
                    <Text size="sm">
                      Started {formatAdminDate(membership.startedAt)} ·{" "}
                      {membership.assignedBy}
                    </Text>
                    <Text size="sm" tone="muted">
                      {membership.reason}
                    </Text>
                  </Stack>
                </Card>
              </li>
            ))}
          </Stack>
        ) : (
          <EmptyState
            title="No assignments recorded"
            description="This Agency has not received a Customer assignment yet."
          />
        )}
      </Section>

      <Section title="Activity" description="Consequential Agency changes and assignments.">
        <ActivityTimeline
          activity={data.activityTimeline}
          emptyDescription="No administrator has changed this Agency yet."
        />
      </Section>

      <Section
        title="Lifecycle"
        description="Deactivation blocks assignments. Deletion keeps existing dependency and tombstone rules."
      >
        <Card>
          <Stack gap={3} align="flex-start">
            {data.deletion.requiresDeactivation ? (
              <Text tone="warning" size="sm">
                Deactivate this Agency before deletion.
              </Text>
            ) : dependencies.length ? (
              <Stack gap={2}>
                <Text tone="warning" size="sm">
                  Permanent deletion is blocked by retained history.
                </Text>
                <ul className="admin-agencies__dependencies">
                  {dependencies.map(([key, count]) => (
                    <li key={key}>
                      {DEPENDENCY_LABELS[key] ?? key}: {count.toLocaleString()}
                    </li>
                  ))}
                </ul>
              </Stack>
            ) : null}
            <Button
              variant="danger"
              disabled={data.deletion.requiresDeactivation}
              onClick={() => open("delete")}
            >
              Delete Agency
            </Button>
          </Stack>
        </Card>
      </Section>

      <Dialog
        open={dialog === "edit"}
        onClose={() => setDialog(null)}
        title="Edit Agency"
        description="The stable slug and permanent history do not change."
      >
        <form onSubmit={edit}>
          <Stack gap={4}>
            {failure ? (
              <Text role="alert" tone="danger" size="sm">
                {failure}
              </Text>
            ) : null}
            <Field label="Agency name" required>
              <Input name="name" defaultValue={data.agency.name} autoFocus />
            </Field>
            <Field label="Reason" required>
              <Input name="reason" />
            </Field>
            <Cluster gap={3}>
              <Button type="submit" variant="primary" busy={busy}>
                Save Agency
              </Button>
              <Button onClick={() => setDialog(null)}>Cancel</Button>
            </Cluster>
          </Stack>
        </form>
      </Dialog>

      <ConfirmDialog
        open={dialog === "lifecycle"}
        onClose={() => setDialog(null)}
        onConfirm={() =>
          void update(data.agency.name, !data.agency.active, reason)
        }
        title={data.agency.active ? "Deactivate Agency" : "Reactivate Agency"}
        consequence={
          data.agency.active
            ? "New assignments and Customer work stop. Membership and permanent history are retained."
            : "New assignments and Customer work are allowed again. Permanent history is unchanged."
        }
        confirmLabel={data.agency.active ? "Deactivate" : "Reactivate"}
        destructive={data.agency.active}
        busy={busy}
      >
        <Stack gap={3}>
          {failure ? (
            <Text role="alert" tone="danger" size="sm">
              {failure}
            </Text>
          ) : null}
          <Field label="Reason" required>
            <Input
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </Field>
        </Stack>
      </ConfirmDialog>

      <ConfirmDialog
        open={dialog === "delete"}
        onClose={() => setDialog(null)}
        onConfirm={() => void remove()}
        title="Delete Agency"
        consequence={
          data.deletion.canHardDelete
            ? "Permanent deletion cannot be undone."
            : "The Agency and its Customers leave administration while permanent history is retained."
        }
        confirmLabel="Delete Agency"
        destructive
        busy={busy}
      >
        <Stack gap={3}>
          {failure ? (
            <Text role="alert" tone="danger" size="sm">
              {failure}
            </Text>
          ) : null}
          {data.deletion.canHardDelete ? (
            <label className="admin-agencies__toggle">
              <input
                type="checkbox"
                checked={hardDelete}
                onChange={(event) => setHardDelete(event.target.checked)}
              />
              <span>Permanently delete this empty Agency</span>
            </label>
          ) : null}
          <Field label={`Type ${data.agency.slug} to confirm`} required>
            <Input
              value={confirmSlug}
              onChange={(event) => setConfirmSlug(event.target.value)}
            />
          </Field>
          <Field label="Reason" required>
            <Input
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </Field>
        </Stack>
      </ConfirmDialog>
    </Page>
  );
}
