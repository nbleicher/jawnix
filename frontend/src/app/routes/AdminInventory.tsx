import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { useLoaderData, useNavigate, useRevalidator } from "react-router";

import { ActionLink, Button, ButtonLink } from "../../design-system/primitives/Button";
import { FactList } from "../../design-system/primitives/facts";
import { Field, Input } from "../../design-system/primitives/form";
import { Page, Section } from "../../design-system/primitives/layout";
import { Panel } from "../../design-system/primitives/panel";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { Text } from "../../design-system/primitives/typography";
import { api } from "../api";
import { errorMessage } from "../operator/actions";
import { formatCount, formatDay, plural } from "../operator/format";
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
      // A hidden tab keeps the last known state; the next visible tick reads
      // again on the same cadence.
      if (document.visibilityState === "hidden") return;
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

// The tool pages live in their own modules; these keep the imports that
// already name this module working.
export { BackToInventory, Pager } from "./inventoryTools";
export type { Paged } from "./inventoryTools";
export {
  AdminSuppressedLeadsRoute,
  adminSuppressedLeadsLoader,
} from "./AdminSuppressedLeads";
export type { SuppressedLeadRow, SuppressedLeadsData } from "./AdminSuppressedLeads";
export { AdminNicheAssignmentRoute } from "./AdminNicheAssignment";
export {
  AdminPoolAnalyticsRoute,
  adminPoolAnalyticsLoader,
} from "./AdminPoolAnalytics";
export type { PoolBreakdownCell, PoolBreakdownData } from "./AdminPoolAnalytics";
