import type { ReactNode } from "react";
import type { LoaderFunctionArgs } from "react-router";
import { Link, useLoaderData } from "react-router";

import { ActionLink } from "../../design-system/primitives/Button";
import { FactList } from "../../design-system/primitives/facts";
import { Panel, RecordColumns } from "../../design-system/primitives/panel";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { Page, Section } from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import type { StatusTone } from "../../design-system/primitives/status";
import { Mono, Text } from "../../design-system/primitives/typography";
import { api } from "../api";
import { routePath } from "../routePath";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { ControlActionBar, conflictEndpoint, postReason } from "../operator/actions";
import type { ControlAction } from "../operator/actions";
import { formatDateTime } from "../operator/format";
import type { ActivityPage } from "./AdminActivity";
import type { MilestoneGraphData } from "./batchRequests";

export { routePath };

/**
 * Fulfillment contracts and the Inventory Conflict page (#57).
 *
 * The Fulfillment workspace itself is gone: its queues live on Today and All
 * batches, and its suppressed Leads and pool analytics on Inventory, so
 * /admin/fulfillment redirects to All batches. What stays is the Inventory
 * Conflict page and the wire types the Batch page, side panels, and Lead
 * Report page share.
 *
 * Every action rendered from these contracts comes from the server's
 * `actions` array, projected from jawnix/fulfillment.py — the same table
 * `transition_request` enforces — so a screen can never offer what the domain
 * refuses.
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

export async function fulfillmentConflictLoader({
  params,
}: LoaderFunctionArgs): Promise<ConflictDetail> {
  return api<ConflictDetail>(
    `/api/admin/inventory-conflicts/${params.conflictId}`,
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
