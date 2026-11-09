import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { LoaderFunctionArgs } from "react-router";
import { Link, useLoaderData, useRevalidator } from "react-router";

import { ActionLink, Button } from "../../design-system/primitives/Button";
import { FactList } from "../../design-system/primitives/facts";
import { Panel, RecordColumns } from "../../design-system/primitives/panel";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { EmptyState } from "../../design-system/primitives/feedback";
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
import type { StatusTone } from "../../design-system/primitives/status";
import { Heading, Mono, Text } from "../../design-system/primitives/typography";
import { api } from "../auth/adminMFA";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import {
  ControlActionBar,
  RenotifyAction,
  conflictEndpoint,
  errorMessage,
  leadControlRequest,
  postReason,
  requestEndpoint,
} from "../operator/actions";
import type { ControlAction } from "../operator/actions";
import { formatDateTime } from "../operator/format";
import { loadEntityActivity } from "./AdminActivity";
import type { ActivityPage } from "./AdminActivity";
import type { MilestoneGraphData } from "./batchRequests";
import { useLiveRequests } from "./useLiveRequests";

/**
 * The administrator Fulfillment workspace (#57).
 *
 * Every action rendered here comes from the server's `actions` array, which is
 * projected from jawnix/fulfillment.py — the same table `transition_request`
 * enforces. The screen never decides for itself whether Approve or Retry
 * delivery applies, because an invalid action offered is a bug rather than a
 * UI detail, and a screen that derives validity independently will eventually
 * disagree with the domain.
 */

export {
  ControlActionBar,
  leadControlRequest,
  RenotifyAction,
} from "../operator/actions";
export type {
  ControlAction,
  ControlExtras,
  ControlRequest,
  ControlResolver,
} from "../operator/actions";

export interface FulfillmentAction {
  name: string;
  label: string;
  consequence: string;
  requiresReason: boolean;
  destructive: boolean;
}

export interface RequestSummary {
  id: string;
  /** "Batch #1042". */
  number?: number;
  customer: string;
  customerIdentity: string;
  agency: string;
  email: string;
  leadCount: number;
  states: string[];
  stateMode: string;
  status: string;
  statusMessage: string;
  availableCount: number | null;
  createdAt: string;
  actions: FulfillmentAction[];
}

export interface DeliveryFailure extends RequestSummary {
  lastError: string;
  deliveryAttempts: number;
  deliveryStatus: string;
}

export interface ConflictSide {
  id: string;
  /** "Batch #1042". Absent only when the Batch Request is gone. */
  number?: number;
  customerIdentity?: string;
  agency?: string;
  leadCount?: number;
  states?: string[];
  status?: string;
  createdAt?: string;
  eligibleCount?: number | null;
  available: boolean;
}

export interface ConflictDetail {
  id: string;
  status: string;
  olderRequest: ConflictSide;
  newerRequest: ConflictSide;
  overlappingLeadCount: number;
  snapshotChecksum: string;
  recurrenceRule: string;
  decisionBy: string;
  decisionReason: string;
  decidedAt: string | null;
  consumedAt: string | null;
  createdAt: string;
  actions: FulfillmentAction[];
}

/**
 * Lead Report eligibility controls (#58).
 *
 * A Lead Report is the Customer's account of a bad Lead. It is evidence, so it
 * is never edited here: every control below acts on the *Lead*, and the
 * Distribution Event that was delivered stays exactly as delivered.
 */

/** What the Lead looked like underneath the report — the thing a Lead
 *  Correction overrides. `kind: "none"` means there is nothing underneath. */
export interface LeadEvidence {
  kind: "current_listing" | "legacy_snapshot" | "prior_correction" | "none";
  label: string;
  title: string;
  state: string;
  observationId: number | null;
  observedAt: string | null;
  source: string;
}

export interface LeadCorrection {
  id: string;
  title: string;
  state: string;
  reason: string;
  createdAt: string;
  basedOnKind: string;
  basedOnLabel: string;
  basedOnTitle: string;
  basedOnState: string;
  actions: ControlAction[];
}

export interface LeadReportDetail {
  id: string;
  reason: string;
  reasonLabel: string;
  /** The Customer's own words. Immutable — never rendered into a form control. */
  details: string;
  status: "open" | "dismissed" | "corrected" | "suppressed";
  createdAt: string;
  href: string;
  customer: { id: number; name: string; href: string };
  distributionEvent: {
    id: number;
    phone: string;
    title: string;
    state: string;
    customerName: string;
    agencyName: string;
    deliveredAt: string;
    listingProvenance: Record<string, unknown>;
    requestId: string | null;
    requestNumber?: number | null;
  };
  lead: {
    id: number;
    phone: string;
    title: string;
    state: string;
    suppressed: boolean;
    correction: LeadCorrection | null;
  };
  evidence: LeadEvidence;
  controls: {
    eligibilityHeld: boolean;
    holdId: string | null;
    holdReason: string;
    holdReleasedAt: string | null;
    /** The release rule, in the domain's words. Rendered verbatim. */
    holdRelease: string;
    holdReleasableByCustomer: false;
    suppressed: boolean;
    suppressionReason: string;
    /** What restoring does and does not promise. Rendered verbatim. */
    restoreNotice: string;
    /** Actions on the Lead itself rather than on the report's decision.
     *  Carries Restore once the Lead has been suppressed. */
    actions?: ControlAction[];
  };
  resolution: {
    action: string;
    note: string;
    actorId: string;
    createdAt: string;
  } | null;
  /** Empty once the report is resolved: a decision is taken once. */
  actions: ControlAction[];
}

/** The queue row. Deliberately lighter than the detail: enough to choose what
 *  to open, without building every report's evidence and controls up front. */
export interface LeadReportSummary {
  id: string;
  reason: string;
  reasonLabel: string;
  details: string;
  status: string;
  createdAt: string;
  href: string;
  customer: { id: number; name: string; href: string };
  eligibilityHeld: boolean;
}

export interface EligibilityHold {
  id: string;
  leadId: number;
  leadPhone: string;
  reason: string;
  reasonLabel: string;
  createdAt: string;
  reportId: string;
  reportStatus: string;
  href: string;
  release: string;
}

export interface SuppressedLead {
  id: number;
  phone: string;
  title: string;
  state: string;
  reason: string;
  restoreNotice: string;
  actions: ControlAction[];
}

export interface WorkspaceData {
  batchRequests: RequestSummary[];
  inventoryConflicts: ConflictDetail[];
  deliveryFailures: DeliveryFailure[];
  /** Settled requests whose Batch Artifact file has expired. They appear
   *  nowhere else, because the request itself is finished work. */
  expiredArtifacts: RequestSummary[];
  /* The #58 sections are optional on the wire so a response cached before that
     slice shipped renders an empty section rather than blanking the screen. */
  leadReports?: LeadReportSummary[];
  eligibilityHolds?: EligibilityHold[];
  suppressedLeads?: SuppressedLead[];
  /** Loaded separately from the workspace aggregate so page loads never
   *  trigger a live recount — only the cached snapshot is shown. */
  poolBreakdown?: PoolBreakdownData;
}

export interface PoolBreakdownCell {
  state: string;
  niche: string;
  count: number;
}

export interface PoolBreakdownData {
  asOf: string | null;
  cells: PoolBreakdownCell[];
}

export interface ArtifactState {
  filename: string;
  rowCount: number;
  sha256: string;
  deliveryStatus: string;
  deliveryAttempts: number;
  lastError: string;
  expiresAt: string | null;
  sentAt: string | null;
  available: boolean;
}

export interface HistoryEntry {
  action: string;
  actor: string;
  reason: string;
  recordedAt: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}

/** One background job still moving for this Batch Request. */
export interface LiveJob {
  kind: string;
  label: string;
  status: string;
  attempts: number;
  runAfter: string | null;
  lastError: string | null;
}

/** Why this request's jobs are waiting on the shared worker. Optional for skew. */
export interface LiveWorkerBlocker {
  kind: string;
  label: string;
  status: string;
  jobId: number;
  requestId: string | null;
  detail: string;
}

/** Live activity strip projected by the server. Optional for deploy-skew. */
export interface LiveActivity {
  settled: boolean;
  refreshSeconds: number;
  /** Present when queued jobs are stuck behind work for another request. */
  blocker?: LiveWorkerBlocker | null;
  jobs: LiveJob[];
  /** Newest-first trail of jobs, status, and decisions. Optional for skew. */
  log?: ProgressLogEntry[];
}

/** One row of the Progress activity log. */
export interface ProgressLogEntry {
  id: string;
  at: string | null;
  kind: string;
  label: string;
  detail: string | null;
  tone: StatusTone;
}

/** One cell of the committed Distribution Event composition. */
export interface DistributionCell {
  state: string;
  niche: string;
  count: number;
}

export interface RequestDetail extends RequestSummary {
  activityTimeline: ActivityPage;
  approvedAt: string | null;
  deliveredAt: string | null;
  artifact: ArtifactState | null;
  distribution: {
    committed: number;
    expected: number;
    complete: boolean;
    byState?: DistributionCell[];
  };
  /** Optional for deploy-skew: an older backend still renders the page. */
  milestones?: MilestoneGraphData;
  live?: LiveActivity;
  telegram: { decisionPending: boolean };
  history: HistoryEntry[];
}

/** A Batch Request status rendered as words first, colour only reinforcing. */
const STATUS_TONES: Record<string, StatusTone> = {
  pending: "info",
  approved: "info",
  processing: "info",
  waiting_inventory: "warning",
  generated: "info",
  delivered: "success",
  rejected: "danger",
  canceled: "neutral",
  failed: "danger",
};

const STATUS_LABELS: Record<string, string> = {
  pending: "Pending",
  approved: "Approved",
  processing: "Processing",
  waiting_inventory: "Waiting for inventory",
  generated: "Generated",
  delivered: "Delivered",
  rejected: "Rejected",
  canceled: "Canceled",
  failed: "Failed",
};

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

function statusTone(status: string): StatusTone {
  return STATUS_TONES[status] ?? "neutral";
}

export function formatDate(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  // Unambiguous across time zones, per the audit-trail requirement.
  return parsed.toISOString().replace("T", " ").slice(0, 16) + " UTC";
}

/** Server hrefs are absolute so they work outside the shell too. Inside it the
 *  router owns the `/app` basename, so it has to come back off. */
export function routePath(href: string): string {
  return href.startsWith("/app/") ? href.slice("/app".length) : href;
}

const REPORT_TONES: Record<string, StatusTone> = {
  open: "warning",
  dismissed: "neutral",
  corrected: "info",
  suppressed: "neutral",
};

const REPORT_LABELS: Record<string, string> = {
  open: "Open",
  dismissed: "Dismissed",
  corrected: "Corrected",
  suppressed: "Suppressed",
};

export function reportStatusLabel(status: string): string {
  return REPORT_LABELS[status] ?? status;
}

export function reportStatusTone(status: string): StatusTone {
  return REPORT_TONES[status] ?? "neutral";
}

export async function fulfillmentLoader(): Promise<WorkspaceData> {
  const [workspace, poolBreakdown] = await Promise.all([
    api<Omit<WorkspaceData, "poolBreakdown">>("/api/admin/fulfillment"),
    api<PoolBreakdownData>("/api/admin/pool-breakdown"),
  ]);
  return { ...workspace, poolBreakdown };
}

export async function loadFulfillmentRequest(requestId: string): Promise<RequestDetail> {
  const [details, activityTimeline] = await Promise.all([
    api<Omit<RequestDetail, "activityTimeline">>(
      `/api/admin/requests/${requestId}`,
    ),
    loadEntityActivity("batch_request", requestId),
  ]);
  return { ...details, activityTimeline };
}

export async function fulfillmentConflictLoader({
  params,
}: LoaderFunctionArgs): Promise<ConflictDetail> {
  return api<ConflictDetail>(
    `/api/admin/inventory-conflicts/${params.conflictId}`,
  );
}

function RequestCard({ item, onConfirmed, refresh }: { item: RequestSummary; onConfirmed?: (result: Partial<RequestDetail>) => void; refresh?: () => void }) {
  return (
    <Card as="article">
      <Stack gap={4}>
        <Stack gap={2}>
          <Cluster gap={2} justify="space-between">
            <Heading level={3} size="sm">
              <Link to={`/admin/batches/${item.number ?? item.id}`}>
                {item.customerIdentity}
              </Link>
            </Heading>
            <StatusBadge tone={statusTone(item.status)}>
              {statusLabel(item.status)}
            </StatusBadge>
          </Cluster>
          <Text size="sm" tone="muted">
            {item.leadCount.toLocaleString()} Leads ·{" "}
            {item.states.join(", ") || "No Licensed States"}
            {item.agency ? ` · ${item.agency}` : ""}
          </Text>
          {item.statusMessage ? (
            <Text size="sm">{item.statusMessage}</Text>
          ) : null}
        </Stack>
        <ControlActionBar
          actions={item.actions}
          resolve={postReason(requestEndpoint(item.id))}
          settled="No action is valid in this state."
          onConfirmed={onConfirmed}
          refresh={refresh}
        />
      </Stack>
    </Card>
  );
}

function ConflictCard({ item }: { item: ConflictDetail }) {
  return (
    <Card as="article">
      <Stack gap={4}>
        <Stack gap={2}>
          <Heading level={3} size="sm">
            <Link to={`/admin/fulfillment/conflicts/${item.id}`}>
              {item.newerRequest.customerIdentity ?? "Newer Batch Request"} vs{" "}
              {item.olderRequest.customerIdentity ?? "older Batch Request"}
            </Link>
          </Heading>
          <Text size="sm" tone="muted">
            {item.overlappingLeadCount.toLocaleString()} overlapping Leads
          </Text>
        </Stack>
        <ControlActionBar
          actions={item.actions}
          resolve={postReason(conflictEndpoint(item.id))}
          settled="This Inventory Conflict has already been decided."
        />
      </Stack>
    </Card>
  );
}

function LeadReportCard({ item }: { item: LeadReportSummary }) {
  return (
    <Card as="article">
      <Stack gap={3}>
        <Cluster gap={2} justify="space-between">
          <Heading level={3} size="sm">
            <Link to={routePath(item.href)}>{item.reasonLabel}</Link>
          </Heading>
          <StatusBadge tone={reportStatusTone(item.status)}>
            {reportStatusLabel(item.status)}
          </StatusBadge>
        </Cluster>
        <Text size="sm" tone="muted">
          Reported by {item.customer.name} · {formatDate(item.createdAt)}
        </Text>
        <Text size="sm">{item.details || "No details were given."}</Text>
        {item.eligibilityHeld ? (
          <Text size="sm" tone="warning">
            Eligibility Hold active — this Lead is withheld from distribution
            while the report is open.
          </Text>
        ) : (
          <Text size="sm" tone="muted">
            No Eligibility Hold on this Lead.
          </Text>
        )}
      </Stack>
    </Card>
  );
}

function EligibilityHoldCard({ item }: { item: EligibilityHold }) {
  return (
    <Card as="article">
      <Stack gap={3}>
        <Heading level={3} size="sm">
          <Link to={routePath(item.href)}>{item.reasonLabel}</Link>
        </Heading>
        <Text size="sm" tone="muted">
          <Mono>{item.leadPhone}</Mono> · held {formatDate(item.createdAt)}
        </Text>
        {/* The release rule, in the domain's words. Paraphrasing it here would
            let the screen imply a release path the domain does not have. */}
        <Text size="sm">{item.release}</Text>
      </Stack>
    </Card>
  );
}

function SuppressedLeadCard({ item }: { item: SuppressedLead }) {
  return (
    <Card as="article">
      <Stack gap={4}>
        <Stack gap={2}>
          <Heading level={3} size="sm">
            <Mono>{item.phone}</Mono>
          </Heading>
          <Text size="sm" tone="muted">
            {item.title || "No title"} · {item.state || "No state"}
          </Text>
          <Text size="sm">{item.reason}</Text>
          <Text size="sm" tone="muted">
            {item.restoreNotice}
          </Text>
        </Stack>
        <ControlActionBar
          actions={item.actions}
          resolve={leadControlRequest(item.id)}
          settled="This Lead's suppression cannot be changed from here."
        />
      </Stack>
    </Card>
  );
}

function PoolBreakdownSection({
  breakdown,
}: {
  breakdown: PoolBreakdownData | undefined;
}) {
  const revalidator = useRevalidator();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [queued, setQueued] = useState(false);
  const data = breakdown ?? { asOf: null, cells: [] };

  async function refresh() {
    setBusy(true);
    setError("");
    try {
      await api("/api/admin/pool-breakdown/refresh", {
        method: "POST",
        body: "{}",
      });
      setQueued(true);
      revalidator.revalidate();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <DisclosureSection
      title="Pool analytics"
      description="Inventory composition for Fulfillment. Unmapped Leads are their own Niche bucket. A background job recounts it after each Inventory Sync, DB2 upload, and every night."
      summary={data.cells.length
        ? `${data.cells.reduce((total, cell) => total + cell.count, 0).toLocaleString()} eligible`
        : "Not computed"}
      defaultOpen
    >
      <Card>
        <Stack gap={4} align="flex-start">
          {error ? (
            <Text size="sm" tone="danger" role="alert">
              {error}
            </Text>
          ) : null}
          <Text size="sm" tone="muted">
            As of: {data.asOf ? formatDate(data.asOf) : "Not computed yet"}
          </Text>
          <Text size="sm" tone="muted" role="status">
            {queued
              ? "Refresh queued. Counts update when the background recount finishes."
              : ""}
          </Text>
          {data.cells.length ? (
            <Grid minColumnWidth="12rem" gap={3} aria-label="Pool breakdown">
              {data.cells.map((cell) => (
                <Card as="article" padding={3} key={`${cell.state}-${cell.niche}`}>
                  <Stack gap={1}>
                    <Text size="sm" tone="muted">{cell.state}</Text>
                    <Heading level={3} size="sm">{cell.niche}</Heading>
                    <Text weight="bold">{cell.count.toLocaleString()} eligible</Text>
                  </Stack>
                </Card>
              ))}
            </Grid>
          ) : (
            <EmptyState
              title="No pool snapshot yet"
              description="The background recount fills this in. Opening this page never runs the count."
            />
          )}
          <Button variant="primary" busy={busy} onClick={() => void refresh()}>
            Refresh pool breakdown
          </Button>
          <ActionLink href="/app/admin/customers" variant="ghost">
            Open Customers for per-Customer availability and cooldown forecasts
          </ActionLink>
        </Stack>
      </Card>
    </DisclosureSection>
  );
}

export function AdminFulfillmentRoute() {
  const initial = useLoaderData<WorkspaceData>();
  const revalidator = useRevalidator();
  const [updates, setUpdates] = useState<Record<string, RequestDetail>>({});
  const revisions = useRef<Record<string, number>>({});
  useEffect(() => {
    for (const row of initial.batchRequests) revisions.current[row.id] = (revisions.current[row.id] ?? 0)+1;
    setUpdates({});
  }, [initial]);
  const terminal = new Set(["delivered", "rejected", "canceled"]);
  const batchRequests = initial.batchRequests.flatMap((row) => {
    const updated = updates[row.id];
    return updated && terminal.has(updated.status) ? [] : [updated ?? row];
  });
  const failures = new Map(initial.deliveryFailures.map((row) => [row.id, row]));
  for (const row of Object.values(updates)) {
    if (row.status === "failed" && row.artifact) {
      failures.set(row.id, { ...row, ...row.artifact });
    } else failures.delete(row.id);
  }
  const data = { ...initial, batchRequests, deliveryFailures: [...failures.values()] };
  async function refreshRequests(ids: string[]) {
    const versions = ids.map((id) => revisions.current[id] ?? 0);
    const changed = await Promise.all(ids.map(loadFulfillmentRequest));
    setUpdates((current) => {
      const next = { ...current };
      changed.forEach((row, index) => { if ((revisions.current[row.id] ?? 0) === versions[index]) next[row.id] = row; });
      return next;
    });
  }
  function confirmed(row: Partial<RequestDetail>) {
    if (!row.id || !row.status || !row.actions) { void revalidator.revalidate(); return; }
    const id = row.id;
    revisions.current[id] = (revisions.current[id] ?? 0)+1;
    setUpdates((current) => ({ ...current, [id]: { ...initial.batchRequests.find((item) => item.id === id), ...current[id], ...row } as RequestDetail }));
    void refreshRequests([id]).catch(() => undefined);
  }
  useLiveRequests("/api/admin/requests/events", batchRequests.filter((row) => row.status !== "failed").map((row) => row.id), refreshRequests, () => { void revalidator.revalidate(); });
  useDocumentTitle("Fulfillment");

  // A response cached before #58 shipped carries none of these arrays; an empty
  // section is a truthful reading of that, a crashed page is not.
  const leadReports = data.leadReports ?? [];
  const eligibilityHolds = data.eligibilityHolds ?? [];
  const suppressedLeads = data.suppressedLeads ?? [];

  return (
    <Page
      title="Fulfillment"
      width="data"
      description="Review Batch Requests, Inventory Conflicts, and delivery failures, and take the actions each one currently allows."
    >
      <Stack gap={6}>
        <PoolBreakdownSection breakdown={data.poolBreakdown} />

        <DisclosureSection
          title="Batch Requests"
          description="Requests still moving through Fulfillment Rotation."
          summary={`${data.batchRequests.length} active`}
        >
          {data.batchRequests.length ? (
            <Grid minColumnWidth="20rem">
              {data.batchRequests.map((item) => (
                <RequestCard item={item} key={item.id} onConfirmed={confirmed} refresh={() => { void refreshRequests([item.id]).catch(() => undefined); }} />
              ))}
            </Grid>
          ) : (
            <EmptyState
              title="No Batch Requests need attention"
              description="Every request has been delivered, rejected, or canceled. New requests appear here once a Customer submits one."
            />
          )}
        </DisclosureSection>

        <DisclosureSection
          title="Inventory Conflicts"
          description="Each decision authorizes one allocation attempt against the current inventory snapshot."
          summary={`${data.inventoryConflicts.length} waiting`}
        >
          {data.inventoryConflicts.length ? (
            <Grid minColumnWidth="20rem">
              {data.inventoryConflicts.map((item) => (
                <ConflictCard item={item} key={item.id} />
              ))}
            </Grid>
          ) : (
            <EmptyState
              title="No Inventory Conflicts are waiting"
              description="A conflict appears when a newer Batch Request could consume Leads an older waiting request also needs."
            />
          )}
        </DisclosureSection>

        <DisclosureSection
          title="Lead Reports"
          description="What Customers reported about Leads they received. Each report is evidence and is never edited; the decisions act on the Lead."
          summary={`${leadReports.length} open`}
        >
          {leadReports.length ? (
            <Grid minColumnWidth="20rem">
              {leadReports.map((item) => (
                <LeadReportCard item={item} key={item.id} />
              ))}
            </Grid>
          ) : (
            <EmptyState
              title="No Lead Reports are open"
              description="A Lead Report appears here when a Customer reports a Lead they were delivered. Resolving one never changes what was delivered."
            />
          )}
        </DisclosureSection>

        <DisclosureSection
          title="Eligibility Holds"
          description="Leads withheld from distribution while a Lead Report is open."
          summary={`${eligibilityHolds.length} active`}
        >
          {eligibilityHolds.length ? (
            <Grid minColumnWidth="20rem">
              {eligibilityHolds.map((item) => (
                <EligibilityHoldCard item={item} key={item.id} />
              ))}
            </Grid>
          ) : (
            <EmptyState
              title="No Eligibility Holds are active"
              description="A hold is placed when a Lead Report is filed and is released only by resolving that report. A Customer cannot release one."
            />
          )}
        </DisclosureSection>

        <DisclosureSection
          title="Suppressed Leads"
          description="Leads withheld from every future Batch Request. Restoring one requires a reason and does not promise it will be allocated."
          summary={`${suppressedLeads.length} suppressed`}
        >
          {suppressedLeads.length ? (
            <Grid minColumnWidth="20rem">
              {suppressedLeads.map((item) => (
                <SuppressedLeadCard item={item} key={item.id} />
              ))}
            </Grid>
          ) : (
            <EmptyState
              title="No Leads are suppressed"
              description="Suppressing a Lead removes it from future distribution without deleting what was already delivered."
            />
          )}
        </DisclosureSection>

        <DisclosureSection
          title="Delivery failures"
          description="Batches that generated successfully but did not reach the Customer."
          summary={`${data.deliveryFailures.length} failed`}
        >
          {data.deliveryFailures.length ? (
            <Grid minColumnWidth="20rem">
              {data.deliveryFailures.map((item) => (
                <Card as="article" key={item.id}>
                  <Stack gap={4}>
                    <Stack gap={2}>
                      <Heading level={3} size="sm">
                        <Link to={`/admin/batches/${item.number ?? item.id}`}>
                          {item.customerIdentity}
                        </Link>
                      </Heading>
                      <Text size="sm" tone="danger">
                        {item.lastError || "Delivery failed."}
                      </Text>
                      <Text size="sm" tone="muted">
                        {item.deliveryAttempts} delivery attempt
                        {item.deliveryAttempts === 1 ? "" : "s"}
                      </Text>
                    </Stack>
                    <ControlActionBar
                      actions={item.actions}
                      resolve={postReason(requestEndpoint(item.id))}
                      settled="No recovery action is valid in this state."
                    />
                    <RenotifyAction requestId={item.id} />
                  </Stack>
                </Card>
              ))}
            </Grid>
          ) : (
            <EmptyState
              title="No delivery failures"
              description="Every generated batch reached its Customer. Failures appear here with the exact error the provider reported."
            />
          )}
        </DisclosureSection>

        <DisclosureSection
          title="Expired Batch Artifacts"
          description="Delivered batches whose file has passed its 30-day retention. The history stays permanent and the exact file can be rebuilt."
          summary={`${data.expiredArtifacts.length} expired`}
        >
          {data.expiredArtifacts.length ? (
            <Grid minColumnWidth="20rem">
              {data.expiredArtifacts.map((item) => (
                <RequestCard item={item} key={item.id} />
              ))}
            </Grid>
          ) : (
            <EmptyState
              title="No Batch Artifacts have expired"
              description="A delivered batch's file expires 30 days after it is generated. Expired ones appear here so the exact file can be regenerated on request."
            />
          )}
        </DisclosureSection>
      </Stack>
    </Page>
  );
}


const CONFLICT_STATUSES: Record<string, { label: string; tone: StatusTone }> = {
  pending: { label: "Awaiting decision", tone: "danger" },
  confirmed: { label: "Confirmed once", tone: "info" },
  denied: { label: "Denied", tone: "neutral" },
  consumed: { label: "Attempt used", tone: "neutral" },
};

function conflictStatus(status: string) {
  return CONFLICT_STATUSES[status] ?? { label: status, tone: "neutral" as StatusTone };
}

/** Who decided, in words: Activity keeps identities, not names. */
function deciderName(actor: string): string {
  if (!actor) return "—";
  if (actor.startsWith("telegram:")) return "Operator in Telegram";
  if (actor.startsWith("system")) return "System";
  return "Operator";
}

function conflictBatchLink(side: ConflictSide): ReactNode {
  if (!side.available) return "No longer available";
  return (
    <Link to={`/admin/batches/${side.number ?? side.id}`}>
      {side.number ? `Batch #${side.number}` : "Batch"}
    </Link>
  );
}

/**
 * One Inventory Conflict (P3): the decision first, in plain words, then the
 * two batches it is about, each linked.
 */
export function AdminFulfillmentConflictRoute() {
  const item = useLoaderData<ConflictDetail>();
  const older = item.olderRequest;
  const newer = item.newerRequest;
  const status = conflictStatus(item.status);
  const olderName = older.number ? `Batch #${older.number}` : "the older batch";
  const newerName = newer.number ? `Batch #${newer.number}` : "the newer batch";
  const sentence = `${olderName.charAt(0).toUpperCase()}${olderName.slice(1)} cannot be filled yet, and ${newerName} could use leads both need.`;
  useDocumentTitle("Inventory conflict");

  const sides: { role: string; side: ConflictSide }[] = [
    { role: "Older, waiting", side: older },
    { role: "Newer", side: newer },
  ];
  const columns: DataColumn<{ role: string; side: ConflictSide }>[] = [
    { key: "batch", header: "Batch", cell: ({ side }) => conflictBatchLink(side) },
    { key: "role", header: "Order", cell: ({ role }) => role },
    { key: "customer", header: "Customer", cell: ({ side }) => side.customerIdentity ?? "—" },
    {
      key: "leads",
      header: "Leads",
      numeric: true,
      cell: ({ side }) => (side.leadCount ?? 0).toLocaleString(),
    },
    { key: "states", header: "States", cell: ({ side }) => (side.states ?? []).join(", ") || "—" },
    {
      key: "canGet",
      header: "Can get now",
      numeric: true,
      cell: ({ side }) =>
        side.eligibleCount === null || side.eligibleCount === undefined
          ? "—"
          : side.eligibleCount.toLocaleString(),
    },
    {
      key: "status",
      header: "Status",
      cell: ({ side }) =>
        side.status ? (
          <StatusBadge tone={statusTone(side.status)}>{statusLabel(side.status)}</StatusBadge>
        ) : (
          "—"
        ),
    },
  ];

  return (
    <Page
      title="Inventory conflict"
      width="data"
      density="data"
      description={sentence}
      actions={
        <>
          <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
          <ControlActionBar
            actions={item.actions}
            resolve={postReason(conflictEndpoint(item.id))}
            settled=""
            primaryAction="confirm"
          />
        </>
      }
    >
      <RecordColumns
        main={
          <>
            <Section title="The decision">
              <FactList
                label="The decision"
                items={[
                  {
                    term: "Confirm once",
                    description: `Gives ${newerName} one allocation attempt against this inventory snapshot.`,
                  },
                  {
                    term: "Deny",
                    description: `Keeps ${newerName} waiting behind ${olderName}.`,
                  },
                  {
                    term: "Leads both batches need",
                    description: item.overlappingLeadCount.toLocaleString(),
                  },
                  ...(item.decidedAt
                    ? [
                        { term: "Decision", description: status.label },
                        { term: "Decided by", description: deciderName(item.decisionBy) },
                        { term: "Reason", description: item.decisionReason || "—" },
                        { term: "Decided", description: formatDateTime(item.decidedAt) },
                      ]
                    : []),
                  ...(item.consumedAt
                    ? [{ term: "Attempt used", description: formatDateTime(item.consumedAt) }]
                    : []),
                ]}
              />
              {item.actions.length === 0 ? (
                <Text size="sm" tone="muted">
                  This conflict has been decided and cannot be decided again.
                </Text>
              ) : null}
            </Section>
            <Section title="The two batches">
              <DataTable
                label="The two batches"
                columns={columns}
                rows={sides}
                rowKey={({ role }) => role}
              />
            </Section>
          </>
        }
        aside={
          <>
            <Panel title="How this works">
              <Text size="sm">{item.recurrenceRule}</Text>
            </Panel>
            <Panel title="Inventory snapshot">
              <FactList
                label="Inventory snapshot"
                items={[
                  { term: "Found", description: formatDateTime(item.createdAt) },
                  { term: "Snapshot", description: <Mono>{item.snapshotChecksum.slice(0, 16)}…</Mono> },
                ]}
              />
            </Panel>
            <ActionLink href="/app/admin/today">Back to Today</ActionLink>
          </>
        }
      />
    </Page>
  );
}
