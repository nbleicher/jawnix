import { useState } from "react";
import type { ReactNode } from "react";
import type { LoaderFunctionArgs } from "react-router";
import { Link, useLoaderData, useRevalidator } from "react-router";

import { ActionLink, Button } from "../../design-system/primitives/Button";
import { ConfirmDialog } from "../../design-system/primitives/Dialog";
import { DetailList } from "../../design-system/primitives/detail";
import { EmptyState } from "../../design-system/primitives/feedback";
import { Field, Textarea } from "../../design-system/primitives/form";
import {
  Card,
  Cluster,
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

export interface FulfillmentAction {
  name: string;
  label: string;
  consequence: string;
  requiresReason: boolean;
  destructive: boolean;
}

export interface RequestSummary {
  id: string;
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
export interface ControlAction {
  name: string;
  label: string;
  consequence: string;
  destructive: boolean;
  requiresReason: boolean;
  /** True only for a Lead Correction, which needs a typed title or state on top
   *  of the reason. The screen reads this rather than checking for `correct`,
   *  so the override surface follows the domain instead of a name. */
  requiresOverride?: boolean;
}

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

export interface RequestDetail extends RequestSummary {
  approvedAt: string | null;
  deliveredAt: string | null;
  artifact: ArtifactState | null;
  distribution: { committed: number; expected: number; complete: boolean };
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

function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "The action could not be completed.";
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
  return api<WorkspaceData>("/api/admin/fulfillment");
}

export async function fulfillmentRequestLoader({
  params,
}: LoaderFunctionArgs): Promise<RequestDetail> {
  return api<RequestDetail>(`/api/admin/requests/${params.requestId}`);
}

export async function fulfillmentConflictLoader({
  params,
}: LoaderFunctionArgs): Promise<ConflictDetail> {
  return api<ConflictDetail>(
    `/api/admin/inventory-conflicts/${params.conflictId}`,
  );
}

export interface ControlRequest {
  url: string;
  method: "POST" | "PUT" | "DELETE";
  body: Record<string, unknown>;
}

/** Builds the call one control action makes. Callers own it because the two
 *  families disagree on the wire: a Lead Report decision records the operator's
 *  words as `note`, while a Lead-level suppression or correction takes a
 *  `reason`. Guessing that from the action name here would hide the split. */
export type ControlResolver = (
  action: ControlAction,
  reason: string,
  values: Record<string, string>,
) => ControlRequest;

export interface ControlExtras {
  values: Record<string, string>;
  set: (name: string, value: string) => void;
}

interface ControlActionBarProps {
  actions: ControlAction[];
  resolve: ControlResolver;
  /** Rendered when the record has nothing left to decide. */
  settled: string;
  /** Inputs an action needs beyond the reason, rendered above it. A Lead
   *  Correction uses this to sit the evidence next to the override being
   *  typed, so the operator sees what they are overriding while they type. */
  extras?: (action: ControlAction, control: ControlExtras) => ReactNode;
  /** Checked before anything is sent. Returns the message to show, or "". */
  validate?: (action: ControlAction, values: Record<string, string>) => string;
}

/**
 * Renders exactly the control actions the server offered, each behind its own
 * confirmation stating its own consequence.
 *
 * Three decisions on a Lead Report are three buttons, not one control with a
 * mode: Dismissed, Corrected, and Suppressed do different things to the Lead
 * and a chooser would let one be taken while reading another's consequence.
 */
export function ControlActionBar({
  actions,
  resolve,
  settled,
  extras,
  validate,
}: ControlActionBarProps) {
  const [pending, setPending] = useState<ControlAction | null>(null);
  const [reason, setReason] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const revalidator = useRevalidator();

  function open(action: ControlAction) {
    setPending(action);
    // Nothing is pre-filled: an override must be typed, never defaulted into
    // agreement with the evidence it is supposed to replace.
    setReason("");
    setValues({});
    setError("");
  }

  function close() {
    setPending(null);
    setReason("");
    setValues({});
  }

  async function confirm() {
    if (!pending) return;
    if (pending.requiresReason && !reason.trim()) {
      setError("A reason is required.");
      return;
    }
    const invalid = validate?.(pending, values) ?? "";
    if (invalid) {
      setError(invalid);
      return;
    }
    const request = resolve(pending, reason.trim(), values);
    setBusy(true);
    setError("");
    try {
      await api(request.url, {
        method: request.method,
        body: JSON.stringify(request.body),
      });
      close();
      revalidator.revalidate();
    } catch (caught) {
      setError(errorMessage(caught));
      // A 409 means this report was already decided somewhere else. Re-read it
      // so the screen stops offering a decision that has already been taken.
      revalidator.revalidate();
    } finally {
      setBusy(false);
    }
  }

  if (!actions.length) {
    return (
      <Text size="sm" tone="muted">
        {settled}
      </Text>
    );
  }

  return (
    <>
      <Cluster gap={2}>
        {actions.map((action) => (
          <Button
            key={action.name}
            variant={action.destructive ? "danger" : "secondary"}
            onClick={() => open(action)}
          >
            {action.label}
          </Button>
        ))}
      </Cluster>
      <ConfirmDialog
        open={pending !== null}
        onClose={close}
        onConfirm={() => void confirm()}
        title={pending?.label ?? ""}
        consequence={pending?.consequence ?? ""}
        confirmLabel={pending?.label ?? "Confirm"}
        destructive={pending?.destructive ?? false}
        busy={busy}
      >
        <Stack gap={4}>
          {pending && extras
            ? extras(pending, {
                values,
                set: (name, value) =>
                  setValues((current) => ({ ...current, [name]: value })),
              })
            : null}
          <Field
            label="Reason"
            description="Recorded in Activity so this decision can be explained later."
            required={pending?.requiresReason ?? true}
          >
            <Textarea
              name="reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </Field>
          {error ? (
            <Text size="sm" tone="danger" role="alert">
              {error}
            </Text>
          ) : null}
        </Stack>
      </ConfirmDialog>
    </>
  );
}

/** Lead-level controls. They change the Lead's eligibility; they never touch
 *  the Lead Report's text or the Distribution Event that was delivered. */
export function leadControlRequest(leadId: number): ControlResolver {
  return (action, reason) => {
    if (action.name === "remove-correction") {
      return {
        url: `/api/admin/leads/${leadId}/correction`,
        method: "DELETE",
        body: { reason },
      };
    }
    return {
      url: `/api/admin/leads/${leadId}/suppression`,
      method: action.name === "restore" ? "DELETE" : "PUT",
      body: { reason },
    };
  };
}

/** Adapts the Batch Request and Inventory Conflict surfaces, which all POST
 *  `{reason}` to a per-action endpoint, onto the one confirm flow. */
function postReason(
  endpoint: (action: ControlAction) => string,
): ControlResolver {
  return (action, reason) => ({
    url: endpoint(action),
    method: "POST",
    body: { reason },
  });
}

function requestEndpoint(id: string) {
  return (action: ControlAction) =>
    action.name === "regenerate"
      ? `/api/admin/requests/${id}/artifact/regenerate`
      : `/api/admin/requests/${id}/${action.name}`;
}

function conflictEndpoint(id: string) {
  return (action: ControlAction) =>
    `/api/admin/inventory-conflicts/${id}/${action.name}`;
}

function RequestCard({ item }: { item: RequestSummary }) {
  return (
    <Card as="article">
      <Stack gap={4}>
        <Stack gap={2}>
          <Cluster gap={2} justify="space-between">
            <Heading level={3} size="sm">
              <Link to={`/admin/fulfillment/requests/${item.id}`}>
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

export function AdminFulfillmentRoute() {
  const data = useLoaderData<WorkspaceData>();
  useDocumentTitle("Fulfillment");

  // A response cached before #58 shipped carries none of these arrays; an empty
  // section is a truthful reading of that, a crashed page is not.
  const leadReports = data.leadReports ?? [];
  const eligibilityHolds = data.eligibilityHolds ?? [];
  const suppressedLeads = data.suppressedLeads ?? [];

  return (
    <Page
      title="Fulfillment"
      description="Review Batch Requests, Inventory Conflicts, and delivery failures, and take the actions each one currently allows."
    >
      <Stack gap={6}>
        <Section
          title="Batch Requests"
          description="Requests still moving through Fulfillment Rotation."
        >
          {data.batchRequests.length ? (
            <Grid minColumnWidth="20rem">
              {data.batchRequests.map((item) => (
                <RequestCard item={item} key={item.id} />
              ))}
            </Grid>
          ) : (
            <EmptyState
              title="No Batch Requests need attention"
              description="Every request has been delivered, rejected, or canceled. New requests appear here once a Customer submits one."
            />
          )}
        </Section>

        <Section
          title="Inventory Conflicts"
          description="Each decision authorizes one allocation attempt against the current inventory snapshot."
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
        </Section>

        <Section
          title="Lead Reports"
          description="What Customers reported about Leads they received. Each report is evidence and is never edited; the decisions act on the Lead."
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
        </Section>

        <Section
          title="Eligibility Holds"
          description="Leads withheld from distribution while a Lead Report is open."
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
        </Section>

        <Section
          title="Suppressed Leads"
          description="Leads withheld from every future Batch Request. Restoring one requires a reason and does not promise it will be allocated."
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
        </Section>

        <Section
          title="Delivery failures"
          description="Batches that generated successfully but did not reach the Customer."
        >
          {data.deliveryFailures.length ? (
            <Grid minColumnWidth="20rem">
              {data.deliveryFailures.map((item) => (
                <Card as="article" key={item.id}>
                  <Stack gap={4}>
                    <Stack gap={2}>
                      <Heading level={3} size="sm">
                        <Link to={`/admin/fulfillment/requests/${item.id}`}>
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
        </Section>

        <Section
          title="Expired Batch Artifacts"
          description="Delivered batches whose file has passed its 30-day retention. The history stays permanent and the exact file can be rebuilt."
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
        </Section>
      </Stack>
    </Page>
  );
}

export function AdminFulfillmentRequestRoute() {
  const item = useLoaderData<RequestDetail>();
  useDocumentTitle(`Batch Request · ${item.customerIdentity}`);

  return (
    <Page
      title={`Batch Request — ${item.customerIdentity}`}
      description={item.statusMessage || "Review this Batch Request and act on it."}
      actions={
        <ActionLink href="/app/admin/fulfillment">Back to Fulfillment</ActionLink>
      }
    >
      <Stack gap={6}>
        <Section
          title="Request"
          description="The Customer, scope, and current state this decision applies to."
        >
          <Card>
            <DetailList
              label="Batch Request"
              items={[
                // CONTEXT.md: the Customer is the durable party; the
                // person-with-a-login is a replaceable User Account.
                { term: "Customer", description: item.customerIdentity },
                { term: "User Account", description: item.customer },
                { term: "Agency", description: item.agency || "Standalone Customer" },
                { term: "Delivery email", description: item.email },
                {
                  term: "Quantity",
                  description: `${item.leadCount.toLocaleString()} Leads`,
                },
                {
                  term: "Licensed State scope",
                  description: item.states.join(", ") || "None",
                },
                {
                  term: "Status",
                  description: (
                    <StatusBadge tone={statusTone(item.status)}>
                      {statusLabel(item.status)}
                    </StatusBadge>
                  ),
                },
                {
                  term: "Availability",
                  description:
                    item.availableCount === null
                      ? "Not yet evaluated"
                      : `${item.availableCount.toLocaleString()} eligible Leads at the last attempt`,
                },
                {
                  term: "Distribution",
                  description: `${item.distribution.committed.toLocaleString()} of ${item.distribution.expected.toLocaleString()} committed`,
                },
                { term: "Submitted", description: formatDate(item.createdAt) },
              ]}
            />
          </Card>
        </Section>

        <Section
          title="Actions"
          description="Only the actions this Batch Request's current state allows."
        >
          <Stack gap={3}>
            {item.telegram.decisionPending ? (
              /* Provenance, not a second action surface: the same decision is
                 live in Telegram, and taking it in either place settles it. */
              <Text size="sm" tone="muted">
                This decision is also open in Telegram. Acting here settles it in
                both places.
              </Text>
            ) : null}
            <ControlActionBar
              actions={item.actions}
              resolve={postReason(requestEndpoint(item.id))}
              settled="This Batch Request has reached a state with no valid actions."
            />
          </Stack>
        </Section>

        <Section
          title="Batch Artifact"
          description="The exact CSV materialization of this request."
        >
          {item.artifact ? (
            <Card>
              <DetailList
                label="Batch Artifact"
                items={[
                  { term: "Filename", description: item.artifact.filename },
                  {
                    term: "Rows",
                    description: item.artifact.rowCount.toLocaleString(),
                  },
                  {
                    term: "Checksum",
                    description: <Mono>{item.artifact.sha256}</Mono>,
                  },
                  {
                    term: "Delivery status",
                    description: item.artifact.deliveryStatus,
                  },
                  {
                    term: "Delivery attempts",
                    description: String(item.artifact.deliveryAttempts),
                  },
                  {
                    term: "File",
                    description: item.artifact.available
                      ? "Available"
                      : "Expired — it can be regenerated from committed Distribution Events",
                  },
                  {
                    term: "Expires",
                    description: formatDate(item.artifact.expiresAt),
                  },
                  ...(item.artifact.lastError
                    ? [
                        {
                          term: "Last error",
                          description: item.artifact.lastError,
                        },
                      ]
                    : []),
                ]}
              />
            </Card>
          ) : (
            <EmptyState
              title="No Batch Artifact yet"
              description="A Batch Artifact is created when allocation commits. Until then there is nothing to deliver or regenerate."
            />
          )}
        </Section>

        <Section
          title="History"
          description="Every recorded decision, most recent first."
        >
          {item.history.length ? (
            <Stack gap={3}>
              {item.history.map((entry) => (
                <Card as="article" key={`${entry.action}-${entry.recordedAt}`}>
                  <Stack gap={2}>
                    <Cluster gap={2} justify="space-between">
                      <Heading level={3} size="sm">
                        {entry.action}
                      </Heading>
                      <Text size="sm" tone="muted">
                        {formatDate(entry.recordedAt)}
                      </Text>
                    </Cluster>
                    <Text size="sm">{entry.reason}</Text>
                    <Text size="sm" tone="muted">
                      {entry.actor}
                      {entry.before && entry.after
                        ? ` · ${String((entry.before as { status?: string }).status ?? "")} → ${String((entry.after as { status?: string }).status ?? "")}`
                        : ""}
                    </Text>
                  </Stack>
                </Card>
              ))}
            </Stack>
          ) : (
            <EmptyState
              title="Nothing has been recorded yet"
              description="Consequential actions on this Batch Request will appear here as they are taken."
            />
          )}
        </Section>
      </Stack>
    </Page>
  );
}

export function AdminFulfillmentConflictRoute() {
  const item = useLoaderData<ConflictDetail>();
  useDocumentTitle("Inventory Conflict");

  function side(request: ConflictSide, role: string) {
    return (
      <Card as="article">
        <Stack gap={3}>
          <Heading level={3} size="sm">
            {role}
          </Heading>
          {request.available ? (
            <DetailList
              label={role}
              items={[
                { term: "Customer", description: request.customerIdentity ?? "—" },
                { term: "Agency", description: request.agency || "Standalone Customer" },
                {
                  term: "Quantity",
                  description: `${(request.leadCount ?? 0).toLocaleString()} Leads`,
                },
                {
                  term: "Licensed State scope",
                  description: (request.states ?? []).join(", ") || "None",
                },
                {
                  term: "Status",
                  description: (
                    <StatusBadge tone={statusTone(request.status ?? "")}>
                      {statusLabel(request.status ?? "")}
                    </StatusBadge>
                  ),
                },
                {
                  term: "Eligible at snapshot",
                  description:
                    request.eligibleCount === null ||
                    request.eligibleCount === undefined
                      ? "—"
                      : request.eligibleCount.toLocaleString(),
                },
                { term: "Submitted", description: formatDate(request.createdAt ?? null) },
              ]}
            />
          ) : (
            <Text size="sm" tone="muted">
              This Batch Request is no longer available.
            </Text>
          )}
        </Stack>
      </Card>
    );
  }

  return (
    <Page
      title="Inventory Conflict"
      description="An older Batch Request cannot be fulfilled while a newer one could consume Leads eligible for both."
      actions={
        <ActionLink href="/app/admin/fulfillment">Back to Fulfillment</ActionLink>
      }
    >
      <Stack gap={6}>
        <Section
          title="Competing requests"
          description="The older request holds priority unless this attempt is authorized."
        >
          <Grid minColumnWidth="20rem">
            {side(item.olderRequest, "Older Batch Request")}
            {side(item.newerRequest, "Newer Batch Request")}
          </Grid>
        </Section>

        <Section
          title="Decision scope"
          description="What this decision covers, and what it does not."
        >
          <Card>
            <DetailList
              label="Decision scope"
              items={[
                {
                  term: "Overlapping inventory",
                  description: `${item.overlappingLeadCount.toLocaleString()} Leads eligible for both requests`,
                },
                {
                  term: "Snapshot",
                  description: <Mono>{item.snapshotChecksum}</Mono>,
                },
                { term: "Recurrence", description: item.recurrenceRule },
                {
                  term: "Status",
                  description: (
                    <StatusBadge
                      tone={item.status === "pending" ? "warning" : "neutral"}
                    >
                      {item.status}
                    </StatusBadge>
                  ),
                },
                ...(item.decidedAt
                  ? [
                      { term: "Decided", description: formatDate(item.decidedAt) },
                      { term: "Decided by", description: item.decisionBy },
                      { term: "Reason", description: item.decisionReason },
                    ]
                  : []),
                ...(item.consumedAt
                  ? [
                      {
                        term: "Authorization spent",
                        description: formatDate(item.consumedAt),
                      },
                    ]
                  : []),
              ]}
            />
          </Card>
        </Section>

        <Section
          title="Decision"
          description="One decision authorizes one attempt against this exact snapshot."
        >
          <ControlActionBar
            actions={item.actions}
            resolve={postReason(conflictEndpoint(item.id))}
            settled="This Inventory Conflict has already been decided and cannot be decided again."
          />
        </Section>
      </Stack>
    </Page>
  );
}
