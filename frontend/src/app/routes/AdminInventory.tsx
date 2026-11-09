import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import type { LoaderFunctionArgs } from "react-router";
import { Link, useLoaderData, useNavigate, useRevalidator, useSearchParams } from "react-router";

import { ActionLink, Button, ButtonLink } from "../../design-system/primitives/Button";
import { FactList } from "../../design-system/primitives/facts";
import { Field, Input, Select } from "../../design-system/primitives/form";
import { Cluster, Page, Section, Stack } from "../../design-system/primitives/layout";
import { Panel } from "../../design-system/primitives/panel";
import { StatusBadge } from "../../design-system/primitives/status";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { Text } from "../../design-system/primitives/typography";
import { api } from "../auth/adminMFA";
import { ControlActionBar, errorMessage, leadControlRequest } from "../operator/actions";
import type { ControlAction } from "../operator/actions";
import { formatCount, formatDateTime, formatDay, formatPhone, plural } from "../operator/format";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { formatAsOf } from "./OperatorNumbers";
import "./AdminInventory.css";

/**
 * Inventory (P4): usable DB1 and DB2 leads, usable by state, lead lookup, and
 * the occasional inventory tools — DB2 uploads, Exclusion Lists, suppressed
 * Leads, Niche assignment, and pool analytics — each on its own page.
 *
 * Every count over the inventory is a background snapshot: the page shows its
 * as-of time, and "Refresh counts" only queues a recount.
 */

// ------------------------------------------------------------------ Inventory

export interface InventorySnapshot {
  database: "db1" | "db2";
  label: string;
  allRows: number;
  suppressed: number;
  held: number;
  excluded: number;
  usable: number;
  usableByState: Record<string, number>;
  computedAt: string | null;
}

export interface RefreshState {
  status: string;
  jobId: number | null;
}

export interface InventoryData {
  db1: InventorySnapshot | null;
  db2: InventorySnapshot | null;
  refresh: RefreshState;
  addedLast7Days: number;
  lastUpload: { id: string; filename: string; uploadedBy: string; at: string | null } | null;
  sections: {
    uploads: number;
    uploadsInProgress: number;
    exclusionsWaiting: number;
    suppressed: number;
  };
}

interface SnapshotRead {
  db1: InventorySnapshot | null;
  db2: InventorySnapshot | null;
  refresh: RefreshState;
}

export interface StateRow {
  state: string;
  db1: number;
  db2: number;
  total: number;
}

const TOP_STATES = 10;
const REFRESH_POLL_MS = 5_000;

export async function adminInventoryLoader(): Promise<InventoryData> {
  return api<InventoryData>("/api/admin/inventory");
}

/** Both databases' usable counts per state, largest total first. */
export function stateRows(db1: InventorySnapshot | null, db2: InventorySnapshot | null): StateRow[] {
  const one = db1?.usableByState ?? {};
  const two = db2?.usableByState ?? {};
  const states = new Set([...Object.keys(one), ...Object.keys(two)]);
  return [...states]
    .map((state) => {
      const first = one[state] ?? 0;
      const second = two[state] ?? 0;
      return { state, db1: first, db2: second, total: first + second };
    })
    .sort((a, b) => b.total - a.total || a.state.localeCompare(b.state));
}

function signed(value: number, sign: "−" | "+"): string {
  return value ? `${sign}${formatCount(value)}` : "0";
}

function DatabasePanel({
  title,
  snapshot,
  extra,
  now,
}: {
  title: string;
  snapshot: InventorySnapshot | null;
  extra: { term: string; description: string };
  now: Date;
}) {
  return (
    <Panel
      title={title}
      action={
        <span className="jx-inventory-as-of">
          {snapshot?.computedAt ? `As of ${formatAsOf(snapshot.computedAt, now)}` : "Not counted yet"}
        </span>
      }
    >
      <p className="jx-inventory-usable">
        {snapshot ? `${formatCount(snapshot.usable)} usable` : "Not counted yet"}
      </p>
      <FactList
        label={`${title} breakdown`}
        items={
          snapshot
            ? [
                { term: "All rows", description: formatCount(snapshot.allRows) },
                { term: "Suppressed", description: signed(snapshot.suppressed, "−") },
                { term: "On hold", description: signed(snapshot.held, "−") },
                { term: "Excluded", description: signed(snapshot.excluded, "−") },
                extra,
              ]
            : [
                {
                  term: "Count",
                  description: "The background recount fills this in.",
                },
                extra,
              ]
        }
      />
    </Panel>
  );
}

/** Opens lead lookup for a phone number. Shared by Inventory and lead lookup. */
export function LeadLookupForm({ initial = "" }: { initial?: string }) {
  const navigate = useNavigate();
  const [phone, setPhone] = useState(initial);
  useEffect(() => setPhone(initial), [initial]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = phone.trim();
    if (!value) return;
    navigate(`/admin/inventory/lookup?phone=${encodeURIComponent(value)}`);
  }

  return (
    <form className="jx-lookup-form" role="search" aria-label="Lead lookup" onSubmit={submit}>
      <Field label="Phone number">
        <Input
          type="search"
          inputMode="tel"
          autoComplete="off"
          placeholder="(615) 555-0142"
          value={phone}
          onChange={(event) => setPhone(event.target.value)}
        />
      </Field>
      <Button type="submit" variant="primary">
        Look up
      </Button>
    </form>
  );
}

function useInventoryRefresh(initial: RefreshState) {
  const revalidator = useRevalidator();
  const [refresh, setRefresh] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => setRefresh(initial), [initial]);
  const running = refresh.status !== "idle";

  useEffect(() => {
    if (!running) return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      void api<SnapshotRead>("/api/admin/inventory-snapshots")
        .then((next) => {
          if (cancelled) return;
          setRefresh(next.refresh);
          if (next.refresh.status === "idle") void revalidator.revalidate();
        })
        .catch(() => undefined);
    }, REFRESH_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [running, revalidator]);

  async function start() {
    setBusy(true);
    setError("");
    try {
      const next = await api<SnapshotRead>("/api/admin/inventory-snapshots/refresh", {
        method: "POST",
        body: "{}",
      });
      setRefresh(next.refresh);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const message = error
    ? error
    : refresh.status === "running"
      ? "Recounting both databases. The numbers update when it finishes."
      : refresh.status === "queued"
        ? "Recount queued. The numbers update when it finishes."
        : "";
  return { start, busy, running, message, failed: Boolean(error) };
}

export function AdminInventoryRoute() {
  const data = useLoaderData<InventoryData>();
  const [allStates, setAllStates] = useState(false);
  const refresh = useInventoryRefresh(data.refresh);
  const now = new Date();
  useDocumentTitle("Inventory");

  const rows = useMemo(() => stateRows(data.db1, data.db2), [data.db1, data.db2]);
  const shown = allStates ? rows : rows.slice(0, TOP_STATES);
  const totals = rows.reduce(
    (sum, row) => ({ db1: sum.db1 + row.db1, db2: sum.db2 + row.db2, total: sum.total + row.total }),
    { db1: 0, db2: 0, total: 0 },
  );
  const columns: DataColumn<StateRow>[] = [
    {
      key: "state",
      header: "State",
      cell: (row) => row.state,
      footer: `All ${plural(rows.length, "state")}`,
    },
    {
      key: "db1",
      header: "DB1",
      numeric: true,
      cell: (row) => formatCount(row.db1),
      footer: formatCount(totals.db1),
    },
    {
      key: "db2",
      header: "DB2",
      numeric: true,
      cell: (row) => formatCount(row.db2),
      footer: formatCount(totals.db2),
    },
    {
      key: "total",
      header: "Total",
      numeric: true,
      cell: (row) => formatCount(row.total),
      footer: formatCount(totals.total),
    },
  ];

  const lastUpload = data.lastUpload
    ? `${formatDay(data.lastUpload.at)} by ${data.lastUpload.uploadedBy}`
    : "None yet";
  const { sections } = data;

  return (
    <Page
      title="Inventory"
      width="data"
      density="data"
      description="Usable leads in each database, uploads, exclusions, and lead lookup."
      actions={
        <>
          <Button
            variant="secondary"
            busy={refresh.busy}
            busyLabel="Queuing…"
            disabled={refresh.running}
            onClick={() => void refresh.start()}
          >
            Refresh counts
          </Button>
          <ButtonLink href="/app/admin/inventory/uploads">Upload DB2 CSV</ButtonLink>
        </>
      }
    >
      <Text size="sm" tone={refresh.failed ? "danger" : "muted"} role="status" className="jx-inventory-status">
        {refresh.message}
      </Text>
      <div className="jx-inventory-databases">
        <DatabasePanel
          title="DB1 · Operator leads"
          snapshot={data.db1}
          extra={{ term: "Added last 7 days", description: signed(data.addedLast7Days, "+") }}
          now={now}
        />
        <DatabasePanel
          title="DB2 · CSV uploads"
          snapshot={data.db2}
          extra={{ term: "Last upload", description: lastUpload }}
          now={now}
        />
      </div>

      <Panel title="Lead lookup">
        <LeadLookupForm />
      </Panel>

      <Section title="Usable by state">
        <DataTable
          label="Usable by state"
          columns={columns}
          rows={shown}
          rowKey={(row) => row.state}
          empty="No usable leads counted yet."
        />
        <div className="jx-inventory-table-controls">
          {rows.length > TOP_STATES ? (
            <Button variant="secondary" onClick={() => setAllStates((value) => !value)}>
              {allStates ? `Show the top ${TOP_STATES}` : `Show all ${plural(rows.length, "state")}`}
            </Button>
          ) : (
            <span />
          )}
          <ActionLink href="/api/admin/inventory/usable-by-state.csv" download>
            Download CSV
          </ActionLink>
        </div>
      </Section>

      <nav aria-label="Inventory tools">
        <ul className="jx-inventory-tools">
          <li>
            <ActionLink href="/app/admin/inventory/uploads">
              DB2 uploads ({formatCount(sections.uploads)}
              {sections.uploadsInProgress ? `, ${formatCount(sections.uploadsInProgress)} importing` : ""})
            </ActionLink>
          </li>
          <li>
            <ActionLink href="/app/admin/inventory/exclusions">
              Exclusion lists ({formatCount(sections.exclusionsWaiting)} waiting)
            </ActionLink>
          </li>
          <li>
            <ActionLink href="/app/admin/inventory/suppressed">
              Suppressed leads ({formatCount(sections.suppressed)})
            </ActionLink>
          </li>
          <li>
            <ActionLink href="/app/admin/inventory/niche-assignment">Niche assignment</ActionLink>
          </li>
          <li>
            <ActionLink href="/app/admin/inventory/pool">Pool analytics</ActionLink>
          </li>
        </ul>
      </nav>
    </Page>
  );
}

// ---------------------------------------------------------------- Shared bits

export interface Paged<Row> {
  items: Row[];
  page: number;
  pageSize: number;
  total: number;
  pages: number;
}

function pageHref(path: string, params: URLSearchParams, page: number): string {
  const next = new URLSearchParams(params);
  if (page <= 1) next.delete("page");
  else next.set("page", String(page));
  const query = next.toString();
  return `/app${path}${query ? `?${query}` : ""}`;
}

/** "Showing 1–25 of 61" with Previous and Next, for a paged table. */
export function Pager({ path, data, noun }: { path: string; data: Paged<unknown>; noun: string }) {
  const [params] = useSearchParams();
  const first = data.total ? (data.page - 1) * data.pageSize + 1 : 0;
  const last = Math.min(data.total, data.page * data.pageSize);
  return (
    <Cluster gap={3} justify="space-between">
      <Text size="sm" tone="muted">
        {data.total
          ? `Showing ${formatCount(first)}–${formatCount(last)} of ${formatCount(data.total)}`
          : `No ${noun}`}
      </Text>
      {data.pages > 1 ? (
        <nav aria-label={`${noun.charAt(0).toUpperCase()}${noun.slice(1)} pages`} className="jx-pages">
          {data.page > 1 ? (
            <ButtonLink variant="secondary" href={pageHref(path, params, data.page - 1)}>
              Previous
            </ButtonLink>
          ) : null}
          <Text as="span" size="sm">
            Page {data.page} of {data.pages}
          </Text>
          {data.page < data.pages ? (
            <ButtonLink variant="secondary" href={pageHref(path, params, data.page + 1)}>
              Next
            </ButtonLink>
          ) : null}
        </nav>
      ) : null}
    </Cluster>
  );
}

export function BackToInventory() {
  return <ActionLink href="/app/admin/inventory">Back to Inventory</ActionLink>;
}

function pageParam(request: Request): string {
  const page = new URL(request.url).searchParams.get("page");
  return page && /^\d+$/.test(page) ? `?page=${page}` : "";
}

// ---------------------------------------------------------- Suppressed leads

export interface SuppressedLeadRow {
  id: number;
  phone: string;
  title: string;
  state: string;
  reason: string;
  href: string;
  actions: ControlAction[];
}

export interface SuppressedLeadsData extends Paged<SuppressedLeadRow> {
  restoreNotice: string;
}

export async function adminSuppressedLeadsLoader({
  request,
}: LoaderFunctionArgs): Promise<SuppressedLeadsData> {
  return api<SuppressedLeadsData>(`/api/admin/suppressed-leads${pageParam(request)}`);
}

function routePath(href: string): string {
  return href.startsWith("/app/") ? href.slice("/app".length) : href;
}

export function AdminSuppressedLeadsRoute() {
  const data = useLoaderData<SuppressedLeadsData>();
  useDocumentTitle("Suppressed leads");
  const columns: DataColumn<SuppressedLeadRow>[] = [
    {
      key: "phone",
      header: "Phone",
      cell: (row) => <Link to={routePath(row.href)}>{formatPhone(row.phone)}</Link>,
    },
    { key: "title", header: "Title", cell: (row) => row.title || "No title" },
    { key: "state", header: "State", cell: (row) => row.state || "—" },
    { key: "reason", header: "Reason", cell: (row) => row.reason || "No reason recorded" },
    {
      key: "action",
      header: "Action",
      cell: (row) => (
        <ControlActionBar
          actions={row.actions}
          resolve={leadControlRequest(row.id)}
          settled="Restored"
        />
      ),
    },
  ];
  return (
    <Page
      title="Suppressed leads"
      width="data"
      density="data"
      description="Leads withheld from every Customer. Restoring one takes a reason and is recorded in Activity."
    >
      <Text size="sm" tone="muted">
        {data.restoreNotice}
      </Text>
      <DataTable
        label="Suppressed leads"
        columns={columns}
        rows={data.items}
        rowKey={(row) => String(row.id)}
        hasActions
        empty="No leads are suppressed. Suppressing a lead from lead lookup or a Lead Report puts it here."
      />
      <Pager path="/admin/inventory/suppressed" data={data} noun="suppressed leads" />
      <BackToInventory />
    </Page>
  );
}

// ---------------------------------------------------------- Niche assignment

interface NicheAssignmentUploadStatus {
  id: string;
  filename: string;
  status: "queued" | "ingesting" | "complete" | "failed";
  totalRows: number;
  acceptedRows: number;
  invalidRows: number;
  duplicateRows: number;
  skippedMappedRows: number;
  error: string;
}

const NICHE_IMPORT_STATUS: Record<NicheAssignmentUploadStatus["status"], { label: string; tone: "info" | "success" | "danger" }> = {
  queued: { label: "Queued", tone: "info" },
  ingesting: { label: "Importing", tone: "info" },
  complete: { label: "Imported", tone: "success" },
  failed: { label: "Failed", tone: "danger" },
};

/** Niche Assignment transfer, moved here from Customers: export the
 *  inventory with no source-derived Niche, assign one in a CSV, import it. */
export function AdminNicheAssignmentRoute() {
  const [file, setFile] = useState<File | null>(null);
  const [upload, setUpload] = useState<NicheAssignmentUploadStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const [pollAttempt, setPollAttempt] = useState(0);
  useDocumentTitle("Niche assignment");

  useEffect(() => {
    if (!upload || !["queued", "ingesting"].includes(upload.status)) return;
    const timer = window.setTimeout(() => {
      void api<NicheAssignmentUploadStatus>(`/api/admin/niche-assignments/${upload.id}`)
        .then((next) => {
          setFailure("");
          setUpload(next);
        })
        .catch((caught: unknown) => {
          setFailure(errorMessage(caught));
          setPollAttempt((attempt) => attempt + 1);
        });
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [upload, pollAttempt]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      setFailure("Choose a Niche Assignment CSV.");
      return;
    }
    setBusy(true);
    setFailure("");
    const body = new FormData();
    body.append("file", file);
    try {
      setUpload(
        await api<NicheAssignmentUploadStatus>("/api/admin/niche-assignments", {
          method: "POST",
          body,
        }),
      );
    } catch (caught) {
      setFailure(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const status = upload ? NICHE_IMPORT_STATUS[upload.status] : null;
  return (
    <Page
      title="Niche assignment"
      width="data"
      density="data"
      description="Give a Niche to inventory that has none from its source. Source mappings always take precedence."
    >
      <Section title="1. Export unmapped inventory">
        <Text size="sm" tone="muted">
          Downloads phone, title, and state for every lead with no source-derived Niche, so the
          working file can be reviewed outside Jawnix.
        </Text>
        <div>
          <ButtonLink href="/api/admin/niche-assignments/export" variant="secondary" download>
            Export unmapped inventory
          </ButtonLink>
        </div>
      </Section>
      <Section title="2. Import reviewed assignments">
        <form onSubmit={(event) => void submit(event)} aria-label="Import Niche assignments">
          <Stack gap={3} align="flex-start">
            <Field
              label="Niche Assignment CSV"
              description="CSV with phone and niche headers. Rows with a source-derived Niche are skipped."
              required
            >
              <Input
                type="file"
                accept=".csv,text/csv"
                onChange={(event) => {
                  setFile(event.currentTarget.files?.[0] ?? null);
                  setFailure("");
                }}
              />
            </Field>
            <Button type="submit" variant="primary" busy={busy} busyLabel="Importing…" disabled={!file}>
              Import assignments
            </Button>
          </Stack>
        </form>
        {failure ? (
          <Text role="alert" tone="danger">
            {failure}
          </Text>
        ) : null}
      </Section>
      {upload && status ? (
        <Section title="Import status">
          <Panel title={upload.filename} action={<StatusBadge tone={status.tone}>{status.label}</StatusBadge>}>
            {upload.error ? <Text tone="danger">{upload.error}</Text> : null}
            <FactList
              label="Niche Assignment import status"
              items={[
                { term: "Rows", description: formatCount(upload.totalRows) },
                { term: "Assigned", description: formatCount(upload.acceptedRows) },
                { term: "Skipped, source-mapped", description: formatCount(upload.skippedMappedRows) },
                { term: "Invalid", description: formatCount(upload.invalidRows) },
                { term: "Duplicates", description: formatCount(upload.duplicateRows) },
              ]}
            />
          </Panel>
        </Section>
      ) : null}
      <BackToInventory />
    </Page>
  );
}

// ------------------------------------------------------------ Pool analytics

export interface PoolBreakdownCell {
  state: string;
  niche: string;
  count: number;
}

export interface PoolBreakdownData {
  asOf: string | null;
  cells: PoolBreakdownCell[];
  refreshQueued?: boolean;
}

export async function adminPoolAnalyticsLoader(): Promise<PoolBreakdownData> {
  return api<PoolBreakdownData>("/api/admin/pool-breakdown");
}

const POOL_TOP = 25;

/** Pool analytics, moved here from Fulfillment: usable DB1 leads by state and
 *  effective Niche, recounted by the same background job as the totals. */
export function AdminPoolAnalyticsRoute() {
  const loaded = useLoaderData<PoolBreakdownData>();
  const [data, setData] = useState(loaded);
  const [state, setState] = useState("");
  const [all, setAll] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  useEffect(() => setData(loaded), [loaded]);
  useDocumentTitle("Pool analytics");

  const states = useMemo(
    () => [...new Set(data.cells.map((cell) => cell.state))].sort(),
    [data.cells],
  );
  const rows = useMemo(
    () =>
      data.cells
        .filter((cell) => !state || cell.state === state)
        .sort((a, b) => b.count - a.count || a.state.localeCompare(b.state) || a.niche.localeCompare(b.niche)),
    [data.cells, state],
  );
  const shown = all ? rows : rows.slice(0, POOL_TOP);
  const total = rows.reduce((sum, cell) => sum + cell.count, 0);

  async function refresh() {
    setBusy(true);
    setMessage("");
    setFailed(false);
    try {
      setData(await api<PoolBreakdownData>("/api/admin/pool-breakdown/refresh", { method: "POST", body: "{}" }));
      setMessage("Recount queued. The breakdown updates when it finishes.");
    } catch (caught) {
      setFailed(true);
      setMessage(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const columns: DataColumn<PoolBreakdownCell>[] = [
    { key: "state", header: "State", cell: (row) => row.state, footer: state ? `All of ${state}` : "All states" },
    { key: "niche", header: "Niche", cell: (row) => (row.niche === "unmapped" ? "Unmapped" : row.niche) },
    {
      key: "count",
      header: "Usable",
      numeric: true,
      cell: (row) => formatCount(row.count),
      footer: formatCount(total),
    },
  ];

  return (
    <Page
      title="Pool analytics"
      width="data"
      density="data"
      description="Usable DB1 leads by state and Niche. Leads with no Niche are counted as Unmapped."
      actions={
        <Button variant="secondary" busy={busy} busyLabel="Queuing…" onClick={() => void refresh()}>
          Refresh breakdown
        </Button>
      }
    >
      <Text size="sm" tone={failed ? "danger" : "muted"} role="status">
        {message || (data.asOf ? `As of ${formatDateTime(data.asOf)}` : "Not counted yet. The background recount fills this in.")}
      </Text>
      <div className="jx-inventory-filters">
        <Field label="State">
          <Select value={state} onChange={(event) => setState(event.target.value)}>
            <option value="">All states</option>
            {states.map((code) => (
              <option key={code} value={code}>
                {code}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <DataTable
        label="Pool breakdown"
        columns={columns}
        rows={shown}
        rowKey={(row) => `${row.state}:${row.niche}`}
        empty="No breakdown yet. Opening this page never runs the count."
      />
      {rows.length > POOL_TOP ? (
        <div>
          <Button variant="secondary" onClick={() => setAll((value) => !value)}>
            {all ? `Show the top ${POOL_TOP}` : `Show all ${formatCount(rows.length)} rows`}
          </Button>
        </div>
      ) : null}
      <BackToInventory />
    </Page>
  );
}
