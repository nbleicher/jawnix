import { useEffect, useRef, useState } from "react";
import {
  Link,
  useLoaderData,
  useNavigate,
  useNavigation,
  useRevalidator,
  useSearchParams,
} from "react-router";
import type { LoaderFunctionArgs, ShouldRevalidateFunctionArgs } from "react-router";

import { api } from "../api";
import { routePath } from "../routePath";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { ActionLink, Button } from "../../design-system/primitives/Button";
import { Dialog } from "../../design-system/primitives/Dialog";
import { FactList } from "../../design-system/primitives/facts";
import { Field, Input, Select } from "../../design-system/primitives/form";
import { Kpi, KpiRow } from "../../design-system/primitives/kpi";
import { Cluster, Grid, Page, Stack } from "../../design-system/primitives/layout";
import { PanelLayout, SidePanel } from "../../design-system/primitives/panel";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { Text } from "../../design-system/primitives/typography";
import {
  formatCount,
  formatDatabase,
  formatDay,
  formatRate,
  formatStates,
  plural,
  stageLabel,
} from "../operator/format";
import { useSelectedRow } from "../operator/useSelectedRow";
import { ChangeDatabaseAction, PostAdjustmentAction, formatUsdCents } from "./AdminCustomerControls";

import "./AdminCustomers.css";
import { RegistrationReview } from "./RegistrationReview";
import type { PendingRegistration } from "./RegistrationReview";

export type CustomerAdminTone =
  | "neutral"
  | "info"
  | "success"
  | "warning"
  | "danger";

/** Backend-authored standing. The label carries the meaning; the tone only
 *  reinforces it, so no screen re-derives status from raw record state. */
export interface CustomerAdminStatus {
  label: string;
  description: string;
  tone: CustomerAdminTone;
}

export interface CustomerDirectoryAgency {
  id: number;
  name: string;
  active: boolean;
}

/** One thing a Customer needs from the operator, worst first. */
export interface CustomerNeed {
  kind: string;
  label: string;
  href: string;
}

export interface CustomerOpenBatch {
  number: number;
  leadCount: number;
  stage: string;
  href: string;
}

export interface CustomerDirectoryRow {
  id: number;
  slug: string;
  name: string;
  agency_id: number | null;
  agency: string;
  licensed_states: string[];
  /** Standing of the durable party. */
  customer_status: CustomerAdminStatus;
  /** Standing of the replaceable access. Never the same thing. */
  account_status: CustomerAdminStatus;
  account_email: string;
  last_activity_at: string | null;
  problems: string[];
  href: string;
  lead_database?: "db1" | "db2";
  /** Computed server-side: approvals, failed deliveries, shortfalls,
   *  conflicts, open reports, and setup problems, worst first. */
  needs: CustomerNeed[];
  /** Billing on, whichever database the customer draws from. */
  billed: boolean;
  /** Available balance: the ledger less active holds. */
  balance_cents: number;
  held_cents: number;
  held_for: number[];
  lead_rate_cents_per_thousand: number | null;
  open_batches: number;
  open_batch: CustomerOpenBatch | null;
  delivered_30d: number;
  delivered_30d_batches: number;
  last_delivery_at: string | null;
  /** The last availability snapshot; never counted on read. */
  available: number | null;
  available_as_of: string | null;
}

export interface CustomerDirectoryData {
  pending_registrations?: PendingRegistration[];
  filters: {
    query: string;
    status: "all" | "active" | "deactivated";
    agency_id: number | null;
    state: string;
    problems_only: boolean;
    database?: "" | "db1" | "db2";
    billing?: "" | "billed" | "not_billed";
    needs_only?: boolean;
  };
  agencies: CustomerDirectoryAgency[];
  states: string[];
  total: number;
  matched: number;
  counts?: { active: number; deactivated: number };
  customers: CustomerDirectoryRow[];
}

interface CreatedCustomer {
  ok: boolean;
  customerId: number;
  slug: string;
  userId: string;
  mappingConfirmed: boolean;
}

interface ArchivedCustomerMatch {
  id: number;
  name: string;
  slug: string;
  email: string;
}

interface CustomerInvitation {
  name: string;
  email: string;
  first_name: string | undefined;
  last_name: string | undefined;
}

const FILTER_KEYS = ["q", "status", "agency_id", "database", "billing", "needs_only"] as const;

export function formatAdminDate(value: string | null): string {
  if (!value) return "Never";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(
    new Date(value),
  );
}

/** The directory's href is absolute so it is usable anywhere. Inside the shell
 *  we navigate through the router instead, so the loader, the pending
 *  indicator, and the route announcer all still run. */
export function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "The request could not be completed.";
}

/** Filters live in the URL so a view is shareable and the browser's back
 *  button restores the exact result set the operator was looking at. */
export async function adminCustomerDirectoryLoader({
  request,
}: LoaderFunctionArgs): Promise<CustomerDirectoryData> {
  const requested = new URL(request.url).searchParams;
  const query = new URLSearchParams();
  for (const key of FILTER_KEYS) {
    const value = requested.get(key);
    if (value) query.set(key, value);
  }
  const search = query.toString();
  const [directory, registrations] = await Promise.all([
    api<CustomerDirectoryData>(`/api/admin/customers/directory${search ? `?${search}` : ""}`),
    api<PendingRegistration[]>("/api/admin/registrations"),
  ]);
  return { ...directory, pending_registrations: registrations };
}

/** Opening or closing a row's side panel is not a new search: keep the
 *  table as it is rather than reading it again. */
export function adminCustomerDirectoryShouldRevalidate({
  currentUrl,
  nextUrl,
  defaultShouldRevalidate,
}: ShouldRevalidateFunctionArgs): boolean {
  if (currentUrl.pathname !== nextUrl.pathname || currentUrl.href === nextUrl.href) {
    return defaultShouldRevalidate;
  }
  const strip = (url: URL) => {
    const params = new URLSearchParams(url.search);
    params.delete("customer");
    params.sort();
    return params.toString();
  };
  return strip(currentUrl) === strip(nextUrl) ? false : defaultShouldRevalidate;
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
        placeholder="Name, slug, or email"
        value={draft}
        autoComplete="off"
        onChange={(event) => setDraft(event.target.value)}
      />
    </Field>
  );
}

function NeedCell({ row }: { row: CustomerDirectoryRow }) {
  const [first, ...rest] = row.needs;
  if (!first) {
    return (
      <Text as="span" size="sm" tone="muted">
        Nothing
      </Text>
    );
  }
  return (
    <Text as="span" size="sm" tone="danger">
      {first.label}
      {rest.length ? ` · +${rest.length}` : ""}
    </Text>
  );
}

function heldText(row: Pick<CustomerDirectoryRow, "held_cents" | "held_for">): string {
  if (!row.held_cents) return "Nothing held";
  const batches = row.held_for.map((number) => `#${number}`).join(", ");
  return `${formatUsdCents(row.held_cents)} held for ${batches}`;
}

function openBatchText(batch: CustomerOpenBatch, count: number): string {
  const text = `#${batch.number} · ${formatCount(batch.leadCount)} leads · ${stageLabel(batch.stage).toLowerCase()}`;
  return count > 1 ? `${text} · ${formatCount(count - 1)} more` : text;
}

function CustomerPanel({
  row,
  onClose,
  onChanged,
  autoFocus,
  returnFocus,
}: {
  row: CustomerDirectoryRow;
  onClose: () => void;
  onChanged: () => Promise<void> | void;
  autoFocus: boolean;
  returnFocus: () => HTMLElement | null;
}) {
  const database = row.lead_database ?? "db1";
  return (
    <SidePanel
      eyebrow={`Customer · ${row.agency_id === null || !row.agency ? "No agency" : row.agency}`}
      title={row.name}
      onClose={onClose}
      fullHref={row.href}
      autoFocus={autoFocus}
      returnFocus={returnFocus}
      actions={
        <Cluster gap={3}>
          <PostAdjustmentAction customerId={row.id} onChanged={onChanged} />
          <ChangeDatabaseAction customerId={row.id} database={database} onChanged={onChanged} />
        </Cluster>
      }
    >
      <KpiRow label={`${row.name} numbers`} columns={2}>
        <Kpi
          label="Balance"
          value={row.billed ? formatUsdCents(row.balance_cents) : "Not billed"}
          meta={row.billed ? heldText(row) : "Billing off"}
        />
        <Kpi
          label="Available"
          value={row.available === null ? "Not counted" : formatCount(row.available)}
          meta={`${formatDatabase(database)} · ${formatStates(row.licensed_states)}`}
        />
      </KpiRow>
      <FactList
        label={`${row.name} summary`}
        items={[
          {
            term: "Needs",
            description: row.needs.length ? (
              <span className="admin-customers__needs">
                {row.needs.map((need) => (
                  <Link key={`${need.kind}-${need.href}-${need.label}`} to={routePath(need.href)} className="admin-customers__need">
                    {need.label}
                  </Link>
                ))}
              </span>
            ) : (
              "Nothing"
            ),
          },
          {
            term: row.open_batches > 1 ? "Open batches" : "Open batch",
            description: row.open_batch ? (
              <Link to={routePath(row.open_batch.href)}>{openBatchText(row.open_batch, row.open_batches)}</Link>
            ) : (
              "None"
            ),
          },
          { term: "Delivered · 30 days", description: formatCount(row.delivered_30d) },
          {
            term: "Rate",
            description: row.billed ? formatRate(row.lead_rate_cents_per_thousand) : "Not billed",
          },
          {
            term: "User account",
            description: row.account_email || row.account_status.label,
          },
        ]}
      />
    </SidePanel>
  );
}

function CreateCustomerDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const [matches, setMatches] = useState<ArchivedCustomerMatch[]>([]);
  const [invitation, setInvitation] = useState<CustomerInvitation | null>(null);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const text = (key: string) => String(fields.get(key) ?? "").trim();
    const values = {
      name: text("name"),
      email: text("email"),
      first_name: text("first_name") || undefined,
      last_name: text("last_name") || undefined,
    };
    setInvitation(values);
    await sendInvitation(values);
  }

  async function sendInvitation(values: CustomerInvitation, restoreCustomerId?: number, createNewIdentity = false) {
    setBusy(true);
    setFailure("");
    try {
      const created = await api<CreatedCustomer>("/api/admin/customers", {
        method: "POST",
        body: JSON.stringify({
          ...values,
          ...(restoreCustomerId ? { restore_customer_id: restoreCustomerId } : {}),
          ...(createNewIdentity ? { create_new_identity: true } : {}),
        }),
      });
      await navigate(`/admin/customers/${created.customerId}`);
    } catch (caught) {
      const conflict = caught as { detail?: { archivedCustomers?: ArchivedCustomerMatch[] } };
      setMatches(conflict.detail?.archivedCustomers ?? []);
      // The dialog stays open so the operator keeps everything they typed.
      setFailure(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Create Customer"
      description="This creates the durable Customer and emails an invitation to the first User Account. The Customer sets Licensed States from Account after accepting; administrators never set or see a password."
    >
      <form onSubmit={(event) => void submit(event)} onChange={() => setMatches([])}>
        <Stack gap={4}>
          {failure ? (
            <Text role="alert" tone="danger" size="sm">
              {failure}
            </Text>
          ) : null}
          {matches.length > 0 && invitation ? (
            <Stack gap={3}>
              <Text>Confirm the returning customer before restoring previous batches, billing, and lead history.</Text>
              {matches.map((match) => (
                <Stack key={match.id} gap={2}>
                  <Text>{match.name} · {match.slug}{match.email ? ` · ${match.email}` : ""}</Text>
                  <Button busy={busy} onClick={() => void sendInvitation(invitation, match.id)}>
                    Restore {match.slug} and send invitation
                  </Button>
                </Stack>
              ))}
              <Button busy={busy} onClick={() => void sendInvitation(invitation, undefined, true)}>
                Create separate Customer without old history
              </Button>
            </Stack>
          ) : null}
          <Field label="Customer name" required>
            <Input name="name" autoComplete="off" autoFocus disabled={busy} />
          </Field>
          <Field
            label="Email"
            description="The invitation is sent here. It is the address that signs in, not the Customer's identity."
            required
          >
            <Input name="email" type="email" autoComplete="off" disabled={busy} />
          </Field>
          <Grid minColumnWidth="12rem" gap={4}>
            <Field label="First name" description="Enter both names to check for archived customer history.">
              <Input name="first_name" autoComplete="off" disabled={busy} />
            </Field>
            <Field label="Last name">
              <Input name="last_name" autoComplete="off" disabled={busy} />
            </Field>
          </Grid>
          <Cluster gap={3}>
            <Button type="submit" variant="primary" busy={busy} busyLabel="Creating…">
              Create Customer and send invitation
            </Button>
            <Button onClick={onClose}>Cancel</Button>
          </Cluster>
        </Stack>
      </form>
    </Dialog>
  );
}

function describe(data: CustomerDirectoryData, deactivated: boolean): string {
  const counts = data.counts ?? { active: data.total, deactivated: 0 };
  if (deactivated) {
    return `${formatCount(counts.deactivated)} deactivated. History is kept; reactivate one from its page.`;
  }
  return `${formatCount(counts.active)} active, sorted by what each one needs. Select a row to see it beside the list.`;
}

/**
 * Customers (P2): one table, the customers that need something first, with
 * the selected row open beside it. Needs are computed on the server from the
 * same rules as Today, so the two always agree.
 */
export function AdminCustomersRoute() {
  const data = useLoaderData<CustomerDirectoryData>();
  const [params, setParams] = useSearchParams();
  const revalidator = useRevalidator();
  const selection = useSelectedRow("customer");
  const [createOpen, setCreateOpen] = useState(false);
  useDocumentTitle("Customers");

  // Controls follow the address being loaded, so a filter reads as changed
  // the moment it is picked rather than when its results arrive.
  const navigation = useNavigation();
  const shown =
    navigation.location?.pathname === "/admin/customers"
      ? new URLSearchParams(navigation.location.search)
      : params;
  const deactivated = (shown.get("status") ?? "active") === "deactivated";
  const needsOnly = shown.get("needs_only") === "true";
  const [needsChecked, setNeedsChecked] = useState(needsOnly);
  useEffect(() => setNeedsChecked(needsOnly), [needsOnly]);

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

  const rows = data.customers;
  const selectedId = selection.selected ? Number(selection.selected) : null;
  const selectedRow = rows.find((row) => row.id === selectedId) ?? null;
  const needing = rows.filter((row) => row.needs.length).length;
  const counts = data.counts ?? { active: data.total, deactivated: 0 };
  const registrations = data.pending_registrations ?? [];

  const columns: DataColumn<CustomerDirectoryRow>[] = [
    {
      key: "customer",
      header: "Customer",
      cell: (row) => (
        <Link
          to={routePath(row.href)}
          data-panel-trigger={String(row.id)}
          onClick={selection.openFromLink(String(row.id))}
        >
          {row.name}
        </Link>
      ),
      footer: plural(rows.length, "customer"),
    },
    {
      key: "needs",
      header: "Needs",
      cell: (row) => <NeedCell row={row} />,
      footer: `${formatCount(needing)} need${needing === 1 ? "s" : ""} something`,
    },
    { key: "database", header: "DB", cell: (row) => formatDatabase(row.lead_database) },
    {
      key: "balance",
      header: "Balance",
      numeric: true,
      cell: (row) =>
        row.billed ? (
          formatUsdCents(row.balance_cents)
        ) : (
          <Text as="span" size="sm" tone="muted">
            Not billed
          </Text>
        ),
      footer: formatUsdCents(
        rows.reduce((sum, row) => sum + (row.billed ? row.balance_cents : 0), 0),
      ),
    },
    {
      key: "open",
      header: "Open",
      numeric: true,
      cell: (row) => formatCount(row.open_batches),
      footer: formatCount(rows.reduce((sum, row) => sum + row.open_batches, 0)),
    },
    {
      key: "delivered",
      header: "Delivered 30d",
      numeric: true,
      cell: (row) => formatCount(row.delivered_30d),
      footer: formatCount(rows.reduce((sum, row) => sum + row.delivered_30d, 0)),
    },
    {
      key: "last",
      header: "Last delivery",
      cell: (row) => (row.last_delivery_at ? formatDay(row.last_delivery_at) : "Never"),
    },
  ];

  function statusHref(status: "active" | "deactivated") {
    return status === "active" ? "/app/admin/customers" : "/app/admin/customers?status=deactivated";
  }

  return (
    <Page
      title={deactivated ? "Deactivated customers" : "Customers"}
      width="data"
      density="data"
      description={describe(data, deactivated)}
      actions={
        <Button variant="primary" onClick={() => setCreateOpen(true)}>
          Create Customer
        </Button>
      }
    >
      <div className="admin-customers__filters" role="search" aria-label="Filter customers">
        <SearchFilter value={params.get("q") ?? ""} onChange={(value) => setFilter("q", value.trim() || null)} />
        <Field label="Agency">
          <Select
            value={shown.get("agency_id") ?? ""}
            onChange={(event) => setFilter("agency_id", event.target.value || null)}
          >
            <option value="">All agencies</option>
            {data.agencies.map((agency) => (
              <option key={agency.id} value={String(agency.id)}>
                {agency.active ? agency.name : `${agency.name} (deactivated)`}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Database">
          <Select
            value={shown.get("database") ?? ""}
            onChange={(event) => setFilter("database", event.target.value || null)}
          >
            <option value="">DB1 and DB2</option>
            <option value="db1">DB1</option>
            <option value="db2">DB2</option>
          </Select>
        </Field>
        <Field label="Billing">
          <Select
            value={shown.get("billing") ?? ""}
            onChange={(event) => setFilter("billing", event.target.value || null)}
          >
            <option value="">Billed and not billed</option>
            <option value="billed">Billed</option>
            <option value="not_billed">Not billed</option>
          </Select>
        </Field>
        <label className="admin-customers__check">
          <input
            type="checkbox"
            checked={needsChecked}
            onChange={(event) => {
              setNeedsChecked(event.target.checked);
              setFilter("needs_only", event.target.checked ? "true" : null);
            }}
          />
          Needs only
        </label>
      </div>

      <PanelLayout
        panel={
          selectedRow ? (
            <CustomerPanel
              key={selectedRow.id}
              row={selectedRow}
              onClose={selection.close}
              onChanged={() => revalidator.revalidate()}
              autoFocus={selection.autoFocus}
              returnFocus={selection.returnFocus(String(selectedRow.id))}
            />
          ) : null
        }
      >
        <DataTable
          label={deactivated ? "Deactivated customers" : "Customers"}
          columns={columns}
          rows={rows}
          rowKey={(row) => String(row.id)}
          selectedKey={selectedRow ? String(selectedRow.id) : null}
          onRowClick={(row) => selection.select(String(row.id))}
          empty="No customer matches these filters. Widen the search, or create the customer if it does not exist yet."
        />
      </PanelLayout>

      <Cluster gap={4}>
        {deactivated ? (
          <ActionLink href={statusHref("active")}>Active customers ({formatCount(counts.active)})</ActionLink>
        ) : (
          <ActionLink href={statusHref("deactivated")}>
            Deactivated customers ({formatCount(counts.deactivated)})
          </ActionLink>
        )}
        {registrations.length ? (
          <ActionLink href="#registrations">Registrations ({formatCount(registrations.length)} waiting)</ActionLink>
        ) : (
          <Text as="span" size="sm" tone="muted">
            Registrations (0 waiting)
          </Text>
        )}
        <ActionLink href="/app/admin/agencies">Agencies ({formatCount(data.agencies.length)})</ActionLink>
      </Cluster>

      <RegistrationReview items={registrations} />

      <CreateCustomerDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
      />
    </Page>
  );
}
