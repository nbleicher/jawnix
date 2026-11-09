import { useEffect, useRef, useState } from "react";
import { Link, useLoaderData, useNavigate, useNavigation, useRevalidator, useSearchParams } from "react-router";
import type { LoaderFunctionArgs } from "react-router";

import { api } from "../auth/adminMFA";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { Button } from "../../design-system/primitives/Button";
import { ConfirmDialog, Dialog } from "../../design-system/primitives/Dialog";
import { FactList } from "../../design-system/primitives/facts";
import { Field, Input, Select } from "../../design-system/primitives/form";
import { Kpi, KpiRow } from "../../design-system/primitives/kpi";
import { Cluster, Page, Section, Stack } from "../../design-system/primitives/layout";
import { Panel, RecordColumns } from "../../design-system/primitives/panel";
import { StatusBadge } from "../../design-system/primitives/status";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { Text } from "../../design-system/primitives/typography";
import { errorMessage } from "../operator/actions";
import { formatCount, formatDay, formatStates } from "../operator/format";
import { ActivityTable, loadEntityActivity, recordActivityHref } from "./AdminActivity";
import type { ActivityPage } from "./AdminActivity";

import "./AdminAgencies.css";

/**
 * Agencies (Setup): groups of Customers that share permanent no-repeat
 * history. One table with each Agency's members, recent deliveries, and open
 * Batches; each Agency's page links its members and keeps the membership
 * history and Activity that make its shared history explainable.
 */

interface SharedHistory {
  customers: number;
  agencies: number;
  distributedLeads: number;
}

interface AgencyStatus {
  label: string;
  description: string;
  tone: string;
}

export interface AgencyMemberRow {
  id: number;
  slug: string;
  name: string;
  active: boolean;
  licensedStates: string[];
  /** Leads delivered in the last 30 days. */
  delivered30d: number;
  openBatches: number;
  href: string;
}

export interface AgencyDirectoryRow {
  id: number;
  slug: string;
  name: string;
  active: boolean;
  status: AgencyStatus;
  members: AgencyMemberRow[];
  currentMembers: number;
  delivered30d: number;
  openBatches: number;
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
}

export interface AgencyDetailsData {
  activityTimeline: ActivityPage;
  agency: {
    id: number;
    slug: string;
    name: string;
    active: boolean;
    status: AgencyStatus;
  };
  members: AgencyMemberRow[];
  sharedHistory: SharedHistory;
  membershipHistory: {
    id: number;
    customerId: number;
    customer: string;
    customerHref?: string | null;
    startedAt: string;
    endedAt: string | null;
    assignedBy: string;
    assignedByName?: string;
    reason: string;
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
  return api<AgencyDirectoryData>(`/api/admin/agencies/directory${search ? `?${search}` : ""}`);
}

export async function adminAgencyDetailsLoader({
  params,
}: LoaderFunctionArgs): Promise<AgencyDetailsData> {
  const [details, activityTimeline] = await Promise.all([
    api<Omit<AgencyDetailsData, "activityTimeline">>(`/api/admin/agencies/${params.agencyId}/details`),
    loadEntityActivity("agency", params.agencyId),
  ]);
  return { ...details, activityTimeline };
}

/** Black is active; grey is deactivated (V4). */
function agencyBadge(active: boolean) {
  return <StatusBadge tone={active ? "success" : "neutral"}>{active ? "Active" : "Deactivated"}</StatusBadge>;
}

/** A member works only while both it and its Agency are active. */
function memberColumns(agencyActive: boolean): DataColumn<AgencyMemberRow>[] {
  return [
    {
      key: "customer",
      header: "Customer",
      cell: (member) => <Link to={routePath(member.href)}>{member.name}</Link>,
    },
    { key: "states", header: "States", cell: (member) => formatStates(member.licensedStates) },
    {
      key: "delivered",
      header: "Delivered · 30 days",
      numeric: true,
      cell: (member) => formatCount(member.delivered30d),
    },
    { key: "open", header: "Open batches", numeric: true, cell: (member) => formatCount(member.openBatches) },
    { key: "status", header: "Status", cell: (member) => agencyBadge(member.active && agencyActive) },
  ];
}

function CreateAgencyDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
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

function SearchFilter({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const [draft, setDraft] = useState(value);
  const changed = useRef(onChange);
  changed.current = onChange;
  useEffect(() => setDraft(value), [value]);
  useEffect(() => {
    if (draft === value) return;
    const timer = window.setTimeout(() => changed.current(draft), 300);
    return () => window.clearTimeout(timer);
  }, [draft, value]);
  return (
    <Field label="Search">
      <Input
        type="search"
        placeholder="Agency or member"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
      />
    </Field>
  );
}

const AGENCY_COLUMNS: DataColumn<AgencyDirectoryRow>[] = [
  { key: "agency", header: "Agency", cell: (row) => <Link to={routePath(row.href)}>{row.name}</Link> },
  { key: "members", header: "Members", numeric: true, cell: (row) => formatCount(row.currentMembers) },
  {
    key: "delivered",
    header: "Delivered · 30 days",
    numeric: true,
    cell: (row) => formatCount(row.delivered30d),
  },
  { key: "open", header: "Open batches", numeric: true, cell: (row) => formatCount(row.openBatches) },
  {
    key: "history",
    header: "Shared-history leads",
    numeric: true,
    cell: (row) => formatCount(row.sharedHistory.distributedLeads),
  },
  { key: "status", header: "Status", cell: (row) => agencyBadge(row.active) },
  { key: "activity", header: "Last change", cell: (row) => formatDay(row.lastActivityAt) },
];

type AgencyMember = AgencyMemberRow & { agency: AgencyDirectoryRow };

const AGENCY_MEMBER_COLUMNS: DataColumn<AgencyMember>[] = [
  {
    key: "customer",
    header: "Customer",
    cell: (member) => <Link to={routePath(member.href)}>{member.name}</Link>,
  },
  {
    key: "agency",
    header: "Agency",
    cell: (member) => <Link to={routePath(member.agency.href)}>{member.agency.name}</Link>,
  },
  { key: "states", header: "States", cell: (member) => formatStates(member.licensedStates) },
  {
    key: "delivered",
    header: "Delivered · 30 days",
    numeric: true,
    cell: (member) => formatCount(member.delivered30d),
  },
  { key: "open", header: "Open batches", numeric: true, cell: (member) => formatCount(member.openBatches) },
  { key: "status", header: "Status", cell: (member) => agencyBadge(member.active && member.agency.active) },
];

export function AdminAgenciesRoute() {
  const data = useLoaderData<AgencyDirectoryData>();
  const [params, setParams] = useSearchParams();
  const [createOpen, setCreateOpen] = useState(false);
  useDocumentTitle("Agencies");

  const navigation = useNavigation();
  const shown =
    navigation.location?.pathname === "/admin/agencies"
      ? new URLSearchParams(navigation.location.search)
      : params;

  function setFilter(key: string, value: string | null) {
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (value) next.set(key, value);
        else next.delete(key);
        return next;
      },
      { replace: true, preventScrollReset: true },
    );
  }

  const members: AgencyMember[] = data.agencies.flatMap((agency) =>
    agency.members.map((member) => ({ ...member, agency })),
  );

  return (
    <Page
      title="Agencies"
      width="data"
      density="data"
      description="Customers grouped to share permanent no-repeat history. Agencies never sign in."
      actions={
        <Button variant="primary" onClick={() => setCreateOpen(true)}>
          Create Agency
        </Button>
      }
    >
      <div className="jx-agency-filters">
        <SearchFilter value={params.get("q") ?? ""} onChange={(value) => setFilter("q", value.trim() || null)} />
        <Field label="Status">
          <Select value={shown.get("status") ?? "all"} onChange={(event) => setFilter("status", event.target.value === "all" ? null : event.target.value)}>
            <option value="all">All</option>
            <option value="active">Active</option>
            <option value="deactivated">Deactivated</option>
          </Select>
        </Field>
      </div>

      <Section title="Agencies">
        <DataTable
          label="Agencies"
          columns={AGENCY_COLUMNS}
          rows={data.agencies}
          rowKey={(row) => String(row.id)}
          empty="No Agency matches this search. Widen it, or create a new Agency."
        />
        <Text size="sm" tone="muted" role="status">
          Showing {formatCount(data.matched)} of {formatCount(data.total)} agencies.
        </Text>
      </Section>

      <Section title="Members">
        <DataTable
          label="Agency members"
          columns={AGENCY_MEMBER_COLUMNS}
          rows={members}
          rowKey={(member) => String(member.id)}
          empty="No Customer belongs to these agencies."
        />
      </Section>

      <Section title="Independent customers">
        <DataTable
          label="Independent customers"
          columns={memberColumns(true)}
          rows={data.independent}
          rowKey={(member) => String(member.id)}
          empty="No independent customers."
        />
      </Section>

      <CreateAgencyDialog open={createOpen} onClose={() => setCreateOpen(false)} />
    </Page>
  );
}

type DetailsDialog = "edit" | "lifecycle" | "delete";

const DEPENDENCY_LABELS: Record<string, string> = {
  customers: "Current customers",
  distributions: "Distribution events",
  membershipHistory: "Membership history",
};

type Membership = AgencyDetailsData["membershipHistory"][number];

function membershipColumns(now: Date): DataColumn<Membership>[] {
  return [
    {
      key: "customer",
      header: "Customer",
      cell: (membership) =>
        membership.customerHref ? (
          <Link to={routePath(membership.customerHref)}>{membership.customer}</Link>
        ) : (
          membership.customer
        ),
    },
    { key: "started", header: "Joined", cell: (membership) => formatDay(membership.startedAt, now) },
    {
      key: "ended",
      header: "Left",
      cell: (membership) => (membership.endedAt ? formatDay(membership.endedAt, now) : "Current"),
    },
    {
      key: "by",
      header: "Assigned by",
      cell: (membership) => membership.assignedByName ?? membership.assignedBy,
    },
    { key: "reason", header: "Reason", cell: (membership) => membership.reason },
  ];
}

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
  const now = new Date();
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

  const dependencies = Object.entries(data.deletion.dependencies).filter(([, count]) => count > 0);
  const delivered = data.members.reduce((sum, member) => sum + member.delivered30d, 0);
  const openBatches = data.members.reduce((sum, member) => sum + member.openBatches, 0);

  return (
    <Page
      title={data.agency.name}
      width="data"
      density="data"
      description="An Agency: its members share permanent no-repeat history. It has no login."
      actions={
        <Cluster gap={2}>
          <Button onClick={() => open("edit")}>Edit name</Button>
          <Button onClick={() => open("lifecycle")}>
            {data.agency.active ? "Deactivate Agency" : "Reactivate Agency"}
          </Button>
        </Cluster>
      }
    >
      <KpiRow label="Agency numbers" columns={6}>
        <Kpi label="Members" value={formatCount(data.members.length)} />
        <Kpi label="Delivered · 30 days" value={formatCount(delivered)} />
        <Kpi label="Open batches" value={formatCount(openBatches)} />
        <Kpi
          label="Permanent customers"
          value={formatCount(data.sharedHistory.customers)}
          meta="Ever in this shared history"
        />
        <Kpi label="Merged agencies" value={formatCount(data.sharedHistory.agencies)} />
        <Kpi
          label="Shared-history leads"
          value={formatCount(data.sharedHistory.distributedLeads)}
          meta="Never delivered again to members"
        />
      </KpiRow>

      <RecordColumns
        main={
          <>
            <Section title="Members">
              <DataTable
                label="Members"
                columns={memberColumns(data.agency.active)}
                rows={data.members}
                rowKey={(member) => String(member.id)}
                empty="No current members. Customers who moved away still share this history."
              />
            </Section>
            <Section
              title="Membership history"
              description="Every assignment period, kept for audit. Shared history never splits."
            >
              <DataTable
                label="Membership history"
                columns={membershipColumns(now)}
                rows={data.membershipHistory}
                rowKey={(membership) => String(membership.id)}
                empty="This Agency has not received a Customer assignment yet."
              />
            </Section>
            <Section title="Activity">
              <ActivityTable
                activity={data.activityTimeline}
                label="Agency activity"
                showRecord={false}
                moreHref={recordActivityHref("agency", data.agency.id)}
                empty="No administrator has changed this Agency yet."
              />
            </Section>
          </>
        }
        aside={
          <>
            <Panel title="Agency">
              <FactList
                label="Agency"
                items={[
                  { term: "Status", description: agencyBadge(data.agency.active) },
                  { term: "Slug", description: data.agency.slug },
                ]}
              />
              <Text size="sm" tone="muted">
                {data.agency.status.description}
              </Text>
            </Panel>
            <Panel title="Lifecycle">
              {data.deletion.requiresDeactivation ? (
                <Text size="sm" tone="muted">
                  Deactivate this Agency before deleting it.
                </Text>
              ) : dependencies.length ? (
                <>
                  <Text size="sm" tone="muted">
                    Permanent deletion is blocked by retained history:
                  </Text>
                  <FactList
                    label="Retained history"
                    items={dependencies.map(([key, count]) => ({
                      term: DEPENDENCY_LABELS[key] ?? key,
                      description: formatCount(count),
                    }))}
                  />
                </>
              ) : null}
              <div>
                <Button
                  variant="danger"
                  disabled={data.deletion.requiresDeactivation}
                  onClick={() => open("delete")}
                >
                  Delete Agency
                </Button>
              </div>
            </Panel>
          </>
        }
      />

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
        onConfirm={() => void update(data.agency.name, !data.agency.active, reason)}
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
            <Input value={reason} onChange={(event) => setReason(event.target.value)} />
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
            <Input value={confirmSlug} onChange={(event) => setConfirmSlug(event.target.value)} />
          </Field>
          <Field label="Reason" required>
            <Input value={reason} onChange={(event) => setReason(event.target.value)} />
          </Field>
        </Stack>
      </ConfirmDialog>
    </Page>
  );
}

