import { useCallback, useEffect, useRef, useState } from "react";
import type { LoaderFunctionArgs } from "react-router";
import {
  Link,
  useLoaderData,
  useNavigate,
  useNavigation,
  useParams,
  useRevalidator,
  useSearchParams,
} from "react-router";

import { ActionLink, ButtonLink } from "../../design-system/primitives/Button";
import { FactList } from "../../design-system/primitives/facts";
import type { Fact } from "../../design-system/primitives/facts";
import { Field, Input, Select } from "../../design-system/primitives/form";
import {
  Cluster,
  DisclosureSection,
  Page,
  Section,
  Stack,
} from "../../design-system/primitives/layout";
import { Panel, PanelLayout, RecordColumns, Steps } from "../../design-system/primitives/panel";
import { StatusBadge } from "../../design-system/primitives/status";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { Mono, Text } from "../../design-system/primitives/typography";
import { api } from "../auth/adminMFA";
import { formatCents } from "../billing/money";
import {
  ControlActionBar,
  RENOTIFY_STATUSES,
  RenotifyAction,
  postReason,
  requestEndpoint,
} from "../operator/actions";
import { loadBatch } from "../operator/batch";
import type { BatchDetail } from "../operator/batch";
import { BatchPanel, FillResult, availableFact, rotationFact } from "../operator/BatchPanel";
import { LiveActivityStrip, ProgressActivityLog } from "../operator/BatchProgress";
import {
  availabilityText,
  formatAge,
  formatAgo,
  formatCount,
  formatDatabase,
  formatDateTime,
  formatDay,
  formatRate,
  formatStates,
  stageLabel,
  stageTone,
} from "../operator/format";
import type { BatchRow } from "../operator/format";
import { useSelectedRow } from "../operator/useSelectedRow";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import type { HistoryEntry } from "./AdminFulfillment";
import { formatAsOf } from "./OperatorNumbers";
import { useLiveRequests } from "./useLiveRequests";
import "./AdminBatches.css";

/**
 * All batches and the Batch page (P3).
 *
 * All batches lists every Batch, oldest open first — the order they wait in —
 * with the selected row open beside the list. The Batch page replaces the
 * Fulfillment request detail: actions in the header, then whether it can be
 * filled, what it costs, and what has happened, with the Batch, its progress,
 * and the Customer's recent batches on the right.
 */

function routePath(href: string): string {
  return href.startsWith("/app/") ? href.slice("/app".length) : href;
}

// ---------------------------------------------------------------- All batches

export interface BatchListData {
  items: BatchRow[];
  page: number;
  pageSize: number;
  total: number;
  pages: number;
  counts: { delivered: number; expired: number };
}

const STATUS_OPTIONS: [string, string][] = [
  ["open", "Open"],
  ["awaiting_approval", "Awaiting approval"],
  ["waiting", "Waiting for inventory"],
  ["in_progress", "Approved or generating"],
  ["failed", "Failed"],
  ["delivered", "Delivered"],
  ["expired", "Expired files"],
  ["closed", "Rejected or canceled"],
  ["all", "All statuses"],
];

const FILTER_KEYS = ["q", "status", "database", "needsDecision", "page"] as const;

export async function adminBatchesLoader({ request }: LoaderFunctionArgs): Promise<BatchListData> {
  const url = new URL(request.url);
  const query = new URLSearchParams();
  for (const key of FILTER_KEYS) {
    const value = url.searchParams.get(key);
    if (value) query.set(key, value);
  }
  return api<BatchListData>(`/api/admin/batches?${query.toString()}`);
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
        placeholder="Batch number or customer"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
      />
    </Field>
  );
}

function AvailableCell({ row }: { row: BatchRow }) {
  const text = availabilityText(row.availability);
  return row.availability.kind === "short" ? (
    <Text as="span" size="sm" tone="danger">
      {text}
    </Text>
  ) : (
    <>{text}</>
  );
}

export function AdminBatchesRoute() {
  const data = useLoaderData<BatchListData>();
  const [params, setParams] = useSearchParams();
  const revalidator = useRevalidator();
  const selection = useSelectedRow("batch");
  useDocumentTitle("All batches");

  // Controls follow the address being loaded, so a filter reads as changed
  // the moment it is picked rather than when its results arrive.
  const navigation = useNavigation();
  const shown =
    navigation.location?.pathname === "/admin/batches"
      ? new URLSearchParams(navigation.location.search)
      : params;
  const status = shown.get("status") ?? "open";
  const database = shown.get("database") ?? "";
  const needsDecision = shown.get("needsDecision") === "true";
  // The checkbox answers its click at once; the address catches up.
  const [needsChecked, setNeedsChecked] = useState(needsDecision);
  useEffect(() => setNeedsChecked(needsDecision), [needsDecision]);

  function setFilter(key: string, value: string | null) {
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (value) next.set(key, value);
        else next.delete(key);
        if (key !== "page") next.delete("page");
        return next;
      },
      { replace: true, preventScrollReset: true },
    );
  }

  function filterHref(key: string, value: string) {
    const next = new URLSearchParams();
    next.set(key, value);
    return `/app/admin/batches?${next.toString()}`;
  }

  const selectedNumber = selection.selected ? Number(selection.selected) : null;
  const selectedRow = data.items.find((row) => row.number === selectedNumber);
  const columns: DataColumn<BatchRow>[] = [
    {
      key: "batch",
      header: "Batch",
      cell: (row) => (
        <Link
          to={routePath(row.href)}
          data-panel-trigger={String(row.number)}
          onClick={selection.openFromLink(String(row.number))}
        >
          #{row.number}
        </Link>
      ),
    },
    { key: "customer", header: "Customer", cell: (row) => row.customer.name },
    { key: "leads", header: "Leads", numeric: true, cell: (row) => formatCount(row.leadCount) },
    { key: "states", header: "States", cell: (row) => formatStates(row.states) },
    { key: "database", header: "DB", cell: (row) => formatDatabase(row.database) },
    { key: "available", header: "Available", cell: (row) => <AvailableCell row={row} /> },
    {
      key: "status",
      header: "Status",
      cell: (row) => <StatusBadge tone={stageTone(row.stage)}>{stageLabel(row.stage)}</StatusBadge>,
    },
    { key: "age", header: "Age", cell: (row) => formatAge(row.createdAt) },
  ];

  const first = data.total ? (data.page - 1) * data.pageSize + 1 : 0;
  const last = Math.min(data.total, data.page * data.pageSize);

  return (
    <Page
      title="All batches"
      width="data"
      density="data"
      description="Oldest open batch first, the order rotation serves them. Select a row to act on it."
    >
      <div className="jx-batch-filters">
        <SearchFilter value={params.get("q") ?? ""} onChange={(value) => setFilter("q", value.trim() || null)} />
        <Field label="Status">
          <Select value={status} onChange={(event) => setFilter("status", event.target.value === "open" ? null : event.target.value)}>
            {STATUS_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Database">
          <Select value={database} onChange={(event) => setFilter("database", event.target.value || null)}>
            <option value="">DB1 and DB2</option>
            <option value="db1">DB1</option>
            <option value="db2">DB2</option>
          </Select>
        </Field>
        <label className="jx-batch-filters__check">
          <input
            type="checkbox"
            checked={needsChecked}
            onChange={(event) => {
              setNeedsChecked(event.target.checked);
              setFilter("needsDecision", event.target.checked ? "true" : null);
            }}
          />
          Needs a decision
        </label>
      </div>

      <PanelLayout
        panel={
          selectedNumber !== null && Number.isFinite(selectedNumber) ? (
            <BatchPanel
              key={selectedNumber}
              reference={selectedNumber}
              fallbackTitle={
                selectedRow ? `#${selectedRow.number} · ${selectedRow.customer.name}` : `Batch #${selectedNumber}`
              }
              onClose={selection.close}
              onChanged={() => void revalidator.revalidate()}
              autoFocus={selection.autoFocus}
              returnFocus={selection.returnFocus(String(selectedNumber))}
            />
          ) : null
        }
      >
        <DataTable
          label="Batches"
          columns={columns}
          rows={data.items}
          rowKey={(row) => row.id}
          selectedKey={selectedRow?.id ?? null}
          onRowClick={(row) => selection.select(String(row.number))}
          empty="No batch matches these filters."
        />
        <Cluster gap={3} justify="space-between">
          <Text size="sm" tone="muted" role="status">
            {data.total
              ? `Showing ${formatCount(first)}–${formatCount(last)} of ${formatCount(data.total)}`
              : "No batches"}
          </Text>
          {data.pages > 1 ? (
            <nav aria-label="Pages" className="jx-batch-pages">
              {data.page > 1 ? (
                <ButtonLink variant="secondary" href={pageHref(params, data.page - 1)}>
                  Previous
                </ButtonLink>
              ) : null}
              <Text as="span" size="sm">
                Page {data.page} of {data.pages}
              </Text>
              {data.page < data.pages ? (
                <ButtonLink variant="secondary" href={pageHref(params, data.page + 1)}>
                  Next
                </ButtonLink>
              ) : null}
            </nav>
          ) : null}
        </Cluster>
      </PanelLayout>
      <Cluster gap={4}>
        <ActionLink href={filterHref("status", "delivered")}>
          Delivered ({formatCount(data.counts.delivered)})
        </ActionLink>
        <ActionLink href={filterHref("status", "expired")}>
          Expired files ({formatCount(data.counts.expired)})
        </ActionLink>
      </Cluster>
    </Page>
  );
}

function pageHref(params: URLSearchParams, page: number): string {
  const next = new URLSearchParams(params);
  next.delete("batch");
  if (page <= 1) next.delete("page");
  else next.set("page", String(page));
  const query = next.toString();
  return `/app/admin/batches${query ? `?${query}` : ""}`;
}

// ----------------------------------------------------------------- Batch page

export async function adminBatchLoader({ params }: LoaderFunctionArgs): Promise<BatchDetail> {
  return loadBatch(params.batchId ?? "");
}

const ACTION_WORDS: Record<string, string> = {
  batch_request_approve: "Approved",
  batch_request_reject: "Rejected",
  batch_request_cancel: "Canceled",
  batch_request_retry: "Retried generation",
  batch_request_retry_delivery: "Retried the notification",
  batch_request_notify: "Re-notified in Telegram",
  batch_artifact_regenerated: "Regenerated the file",
};

function actionWords(action: string): string {
  const words = ACTION_WORDS[action];
  if (words) return words;
  const spaced = action.replace(/^batch_request_/, "").replaceAll("_", " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** Who did it, in words: Activity stores identities, not names. */
function actorName(actor: string): string {
  if (actor.startsWith("telegram:")) return "Operator in Telegram";
  if (actor.startsWith("system") || actor === "worker") return "System";
  if (actor.includes("@")) return actor;
  return "Operator";
}

interface ActivityRow {
  key: string;
  at: string;
  who: string;
  what: string;
}

function activityRows(batch: BatchDetail): ActivityRow[] {
  const rows: ActivityRow[] = (batch.history ?? []).map((entry: HistoryEntry, index) => ({
    key: `${entry.recordedAt}-${index}`,
    at: entry.recordedAt,
    who: actorName(entry.actor),
    what: entry.reason ? `${actionWords(entry.action)} · ${entry.reason}` : actionWords(entry.action),
  }));
  rows.push({
    key: "submitted",
    at: batch.createdAt,
    who: batch.customer,
    what: `Requested ${formatCount(batch.leadCount)} leads · ${formatStates(batch.states)}`,
  });
  return rows.sort((a, b) => b.at.localeCompare(a.at));
}

const ACTIVITY_COLUMNS: DataColumn<ActivityRow>[] = [
  { key: "when", header: "When", cell: (row) => formatDateTime(row.at) },
  { key: "who", header: "Who", cell: (row) => row.who },
  { key: "what", header: "What", cell: (row) => row.what },
];

function holdWords(batch: BatchDetail): string {
  const amount = formatCents(batch.cost.amountCents ?? 0);
  switch (batch.cost.hold) {
    case "active":
      return `${amount}, held at submission`;
    case "captured":
      return `${amount}, charged at delivery`;
    case "released":
      return `${amount}, hold released`;
    default:
      return amount;
  }
}

function fillFacts(batch: BatchDetail, now: Date): Fact[] {
  const { fill } = batch;
  const facts: Fact[] = [{ term: "Needed", description: formatCount(fill.needed) }];
  if (fill.kind === "committed") {
    facts.push({ term: "Committed", description: formatCount(fill.committed ?? 0) });
  } else {
    const asOf =
      fill.asOf && (fill.kind === "fillable" || fill.kind === "short")
        ? ` · as of ${formatAsOf(fill.asOf, now)}`
        : fill.source === "last_attempt"
          ? " · at the last attempt"
          : "";
    facts.push({
      term: "Available for this customer",
      description: (
        <>
          {availableFact(batch)}
          {asOf}
        </>
      ),
    });
  }
  facts.push({ term: "Result", description: <FillResult batch={batch} /> });
  const rotation = rotationFact(batch);
  if (rotation) facts.push(rotation);
  return facts;
}

function costFacts(batch: BatchDetail): Fact[] {
  if (!batch.cost.billed) {
    return [{ term: "Billing", description: "Not billed" }];
  }
  return [
    { term: "Rate", description: formatRate(batch.cost.rateCentsPerThousand) },
    { term: "This batch", description: holdWords(batch) },
    {
      term: batch.cost.hold === "active" ? "Balance after hold" : "Balance",
      description: formatCents(batch.cost.availableBalanceCents ?? 0),
    },
  ];
}

function fileFacts(batch: BatchDetail, now: Date): Fact[] {
  const artifact = batch.artifact!;
  return [
    { term: "File", description: artifact.filename },
    { term: "Rows", description: formatCount(artifact.rowCount) },
    {
      term: "Available until",
      description: artifact.available ? (
        formatDay(artifact.expiresAt, now)
      ) : (
        <StatusBadge tone="neutral">Expired</StatusBadge>
      ),
    },
    { term: "Notification", description: artifact.deliveryStatus.replaceAll("_", " ") },
    { term: "Notification attempts", description: formatCount(artifact.deliveryAttempts) },
    ...(artifact.lastError
      ? [
          {
            term: "Last error",
            description: (
              <Text as="span" size="sm" tone="danger">
                {artifact.lastError}
              </Text>
            ),
          },
        ]
      : []),
    { term: "Checksum", description: <Mono>{artifact.sha256.slice(0, 16)}…</Mono> },
  ];
}

export function AdminBatchRoute() {
  const initial = useLoaderData<BatchDetail>();
  const [batch, setBatch] = useState(initial);
  const revalidator = useRevalidator();
  const navigate = useNavigate();
  const now = new Date();
  const revision = useRef(0);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      revision.current += 1;
    };
  }, []);
  useEffect(() => {
    revision.current += 1;
    setBatch(initial);
  }, [initial]);
  useDocumentTitle(`Batch #${batch.number}`);

  // A UUID link (older Telegram, email, and Activity links) settles on the
  // number, so the address the operator shares reads "batches/1042".
  const { batchId } = useParams();
  useEffect(() => {
    if (batchId && batchId !== String(initial.number)) {
      navigate(`/admin/batches/${initial.number}`, { replace: true });
    }
  }, [batchId, initial.number, navigate]);

  const refresh = useCallback(async () => {
    const version = revision.current;
    const updated = await loadBatch(initial.number);
    if (mounted.current && revision.current === version) setBatch(updated);
  }, [initial.number]);
  const live = batch.live;
  useLiveRequests(
    `/api/admin/requests/${batch.id}/events`,
    live && !live.settled ? [batch.id] : [],
    refresh,
    () => void revalidator.revalidate(),
  );
  const changed = () => {
    revision.current += 1;
    void refresh().catch(() => undefined);
  };

  const byState = batch.distribution.byState ?? [];
  const steps = batch.progress.map((step) => ({
    key: step.key,
    label: step.label,
    done: step.done,
    time: step.at ? formatAsOf(step.at, now) : null,
  }));

  return (
    <Page
      title={`Batch #${batch.number} · ${batch.customerIdentity}`}
      width="data"
      density="data"
      description={`${formatCount(batch.leadCount)} leads · ${formatStates(batch.states)} · ${formatDatabase(batch.database)} · submitted ${formatAgo(batch.createdAt, now)}`}
      actions={
        <>
          <StatusBadge tone={stageTone(batch.stage)}>{stageLabel(batch.stage)}</StatusBadge>
          <ControlActionBar
            actions={batch.actions}
            resolve={postReason(requestEndpoint(batch.id))}
            settled=""
            primaryAction="approve"
            onConfirmed={changed}
            refresh={changed}
          />
        </>
      }
    >
      <RecordColumns
        main={
          <>
            <Section title="Can it be filled?">
              <FactList label="Can it be filled?" items={fillFacts(batch, now)} />
            </Section>
            <Section title="Cost">
              <FactList label="Cost" items={costFacts(batch)} />
            </Section>
            {batch.artifact ? (
              <Section title="File">
                <FactList label="File" items={fileFacts(batch, now)} />
              </Section>
            ) : null}
            {byState.length ? (
              <Section title="Leads by state">
                <DataTable
                  label="Leads by state"
                  columns={[
                    { key: "state", header: "State", cell: (cell) => cell.state },
                    { key: "niche", header: "Niche", cell: (cell) => cell.niche },
                    {
                      key: "count",
                      header: "Leads",
                      numeric: true,
                      cell: (cell) => formatCount(cell.count),
                      footer: formatCount(byState.reduce((sum, cell) => sum + cell.count, 0)),
                    },
                  ]}
                  rows={byState}
                  rowKey={(cell) => `${cell.state}-${cell.niche}`}
                />
              </Section>
            ) : null}
            <Section title="Activity">
              <DataTable
                label="Activity"
                columns={ACTIVITY_COLUMNS}
                rows={activityRows(batch)}
                rowKey={(row) => row.key}
              />
            </Section>
            <DisclosureSection
              title="Worker log"
              description="Jobs and decisions for this batch as the worker recorded them, newest first."
              summary={live && !live.settled ? "Running" : "Settled"}
            >
              <Stack gap={4}>
                <ProgressActivityLog live={live} />
              </Stack>
            </DisclosureSection>
          </>
        }
        aside={
          <>
            <Panel title="Batch">
              <FactList
                label="Batch"
                items={[
                  {
                    term: "Customer",
                    description: (
                      <Link to={routePath(batch.customerRecord.href)}>{batch.customerRecord.name}</Link>
                    ),
                  },
                  {
                    term: "Agency",
                    description: batch.agencyRecord ? (
                      <Link to={routePath(batch.agencyRecord.href)}>{batch.agencyRecord.name}</Link>
                    ) : (
                      "Standalone"
                    ),
                  },
                  { term: "User account", description: batch.customer },
                  { term: "Delivery email", description: batch.email },
                  { term: "Database", description: formatDatabase(batch.database) },
                  {
                    term: "Rows per file",
                    description: formatCount(batch.rowsPerFile ?? batch.leadCount),
                  },
                ]}
              />
            </Panel>
            <Panel title="Progress">
              <Steps label="Batch progress" steps={steps} />
              <LiveActivityStrip live={live} />
              {RENOTIFY_STATUSES.has(batch.status) ? (
                <RenotifyAction requestId={batch.id} onQueued={changed} />
              ) : null}
              {batch.telegram.decisionPending ? (
                <Text size="xs" tone="muted">
                  Also open in Telegram. Acting here settles it in both places.
                </Text>
              ) : null}
            </Panel>
            <Panel title="Customer's recent batches">
              <FactList
                label="Customer's recent batches"
                items={[
                  ...batch.recentBatches.map((item) => ({
                    term: `#${item.number} · ${formatCount(item.leadCount)} leads`,
                    description: (
                      <Link to={routePath(item.href)}>
                        {item.deliveredAt
                          ? `Delivered ${formatDay(item.deliveredAt, now)}`
                          : `${stageLabel(item.stage)} ${formatDay(item.createdAt, now)}`}
                      </Link>
                    ),
                  })),
                  {
                    term: "Lead reports",
                    description: batch.openLeadReports
                      ? `${formatCount(batch.openLeadReports)} open`
                      : "None open",
                  },
                ]}
              />
              {batch.recentBatches.length === 0 ? (
                <Text size="sm" tone="muted">
                  This is the customer's first batch.
                </Text>
              ) : null}
            </Panel>
            <ActionLink href="/app/admin/batches">All batches</ActionLink>
          </>
        }
      />
    </Page>
  );
}
