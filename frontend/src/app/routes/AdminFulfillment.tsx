import { useState } from "react";
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

export interface WorkspaceData {
  batchRequests: RequestSummary[];
  inventoryConflicts: ConflictDetail[];
  deliveryFailures: DeliveryFailure[];
  /** Settled requests whose Batch Artifact file has expired. They appear
   *  nowhere else, because the request itself is finished work. */
  expiredArtifacts: RequestSummary[];
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

function formatDate(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  // Unambiguous across time zones, per the audit-trail requirement.
  return parsed.toISOString().replace("T", " ").slice(0, 16) + " UTC";
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

interface ActionBarProps {
  actions: FulfillmentAction[];
  /** Resolves the endpoint for one action. Kept out of this component so the
   *  Batch Request and Inventory Conflict surfaces share the confirm flow
   *  without sharing a URL scheme. */
  endpoint: (action: FulfillmentAction) => string;
  /** Rendered when the record has nothing valid left to do. */
  settled: string;
}

/**
 * Renders exactly the actions the server offered, each behind a confirmation
 * that states its consequence and collects the reason the audit trail needs.
 */
function ActionBar({ actions, endpoint, settled }: ActionBarProps) {
  const [pending, setPending] = useState<FulfillmentAction | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const revalidator = useRevalidator();

  function open(action: FulfillmentAction) {
    setPending(action);
    setReason("");
    setError("");
  }

  function close() {
    setPending(null);
    setReason("");
  }

  async function confirm() {
    if (!pending) return;
    // A reason is required for every consequential action, so an empty one
    // never reaches the server and never reaches Activity.
    if (pending.requiresReason && !reason.trim()) {
      setError("A reason is required.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api(endpoint(pending), {
        method: "POST",
        body: JSON.stringify({ reason: reason.trim() }),
      });
      close();
      revalidator.revalidate();
    } catch (caught) {
      setError(errorMessage(caught));
      // A refusal usually means this view is stale — the record moved on, in
      // Telegram or in another tab. Re-read it so the buttons stop offering
      // what the domain has already ruled out.
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
        <Stack gap={3}>
          <Field
            label="Reason"
            description="Recorded in Activity so this decision can be explained later."
            required={pending?.requiresReason ?? true}
            {...(error ? { error } : {})}
          >
            <Textarea
              name="reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </Field>
        </Stack>
      </ConfirmDialog>
    </>
  );
}

function requestEndpoint(id: string) {
  return (action: FulfillmentAction) =>
    action.name === "regenerate"
      ? `/api/admin/requests/${id}/artifact/regenerate`
      : `/api/admin/requests/${id}/${action.name}`;
}

function conflictEndpoint(id: string) {
  return (action: FulfillmentAction) =>
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
        <ActionBar
          actions={item.actions}
          endpoint={requestEndpoint(item.id)}
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
        <ActionBar
          actions={item.actions}
          endpoint={conflictEndpoint(item.id)}
          settled="This Inventory Conflict has already been decided."
        />
      </Stack>
    </Card>
  );
}

export function AdminFulfillmentRoute() {
  const data = useLoaderData<WorkspaceData>();
  useDocumentTitle("Fulfillment");

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
                    <ActionBar
                      actions={item.actions}
                      endpoint={requestEndpoint(item.id)}
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
            <ActionBar
              actions={item.actions}
              endpoint={requestEndpoint(item.id)}
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
          <ActionBar
            actions={item.actions}
            endpoint={conflictEndpoint(item.id)}
            settled="This Inventory Conflict has already been decided and cannot be decided again."
          />
        </Section>
      </Stack>
    </Page>
  );
}
