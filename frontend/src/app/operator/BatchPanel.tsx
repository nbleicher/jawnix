import type { ReactNode } from "react";

import { Button } from "../../design-system/primitives/Button";
import { FactList } from "../../design-system/primitives/facts";
import type { Fact } from "../../design-system/primitives/facts";
import { Stack } from "../../design-system/primitives/layout";
import { SidePanel, Steps } from "../../design-system/primitives/panel";
import { StatusBadge } from "../../design-system/primitives/status";
import { Text } from "../../design-system/primitives/typography";
import { formatCents } from "../billing/money";
import {
  ControlActionBar,
  RENOTIFY_STATUSES,
  RenotifyAction,
  postReason,
  requestEndpoint,
} from "./actions";
import { formatAsOf } from "../routes/OperatorNumbers";
import { useBatch } from "./batch";
import type { BatchDetail } from "./batch";
import {
  formatCount,
  formatDatabase,
  formatDateTime,
  formatRate,
  formatStates,
  plural,
  stageLabel,
} from "./format";

/** "Fillable in full", "Short 31,590", or what the Batch already holds. */
export function FillResult({ batch }: { batch: Pick<BatchDetail, "fill"> }) {
  const { fill } = batch;
  if (fill.kind === "fillable") return <StatusBadge tone="success">Fillable in full</StatusBadge>;
  if (fill.kind === "short") {
    return <StatusBadge tone="danger">Short {formatCount(fill.short ?? 0)}</StatusBadge>;
  }
  if (fill.kind === "committed") return <StatusBadge tone="success">Committed</StatusBadge>;
  if (fill.kind === "none") return <StatusBadge tone="neutral">Closed</StatusBadge>;
  return <Text as="span" size="sm" tone="muted">Not counted yet</Text>;
}

export function availableFact(batch: Pick<BatchDetail, "fill">): ReactNode {
  const { fill } = batch;
  if (fill.kind === "committed") return `${formatCount(fill.committed ?? 0)} committed`;
  if (fill.kind === "fillable" || fill.kind === "short") {
    const value = formatCount(fill.available ?? 0);
    return fill.kind === "short" ? <Text as="span" size="sm" tone="danger">{value}</Text> : value;
  }
  return fill.kind === "none" ? "—" : "Not counted yet";
}

export function costFact(batch: Pick<BatchDetail, "cost">): string {
  const { cost } = batch;
  if (!cost.billed) return "Not billed";
  return `${formatCents(cost.amountCents ?? 0)} at ${formatRate(cost.rateCentsPerThousand)}`;
}

function panelFacts(batch: BatchDetail): Fact[] {
  const facts: Fact[] = [
    {
      term: "Leads",
      description: `${formatCount(batch.leadCount)} · ${formatStates(batch.states)} · ${formatDatabase(batch.database)}`,
    },
    { term: "Available for this customer", description: availableFact(batch) },
    { term: "Result", description: <FillResult batch={batch} /> },
    { term: "Cost", description: costFact(batch) },
  ];
  if (batch.cost.billed) {
    facts.push({
      term: batch.cost.hold === "active" ? "Balance after hold" : "Balance",
      description: formatCents(batch.cost.availableBalanceCents ?? 0),
    });
  }
  if (batch.artifact && (batch.stage === "delivery_failed" || batch.artifact.lastError)) {
    facts.push(
      {
        term: "Notification attempts",
        description: formatCount(batch.artifact.deliveryAttempts),
      },
      { term: "Last error", description: batch.artifact.lastError || "—" },
    );
  }
  if (batch.stage === "failed" && batch.statusMessage) {
    facts.push({ term: "What happened", description: batch.statusMessage });
  }
  facts.push({
    term: "Submitted",
    description: `${formatDateTime(batch.createdAt)} by ${batch.customer}`,
  });
  return facts;
}

/** "$250.00 held at $5.00 per 1,000": what the Customer's page reads. */
function customerCost(batch: BatchDetail): string {
  const { cost } = batch;
  if (!cost.billed) return "Not billed";
  const amount = formatCents(cost.amountCents ?? 0);
  const held = cost.hold === "active" ? " held" : "";
  return `${amount}${held} at ${formatRate(cost.rateCentsPerThousand)}`;
}

/** Facts for a Batch opened on its own Customer's page, where the Customer
 *  is already known and the batch's progress matters more. */
function customerFacts(batch: BatchDetail): Fact[] {
  const { fill } = batch;
  return [
    { term: "States", description: formatStates(batch.states) },
    fill.kind === "committed"
      ? {
          term: "Committed",
          description: `${formatCount(fill.committed ?? 0)} of ${formatCount(batch.leadCount)}`,
        }
      : { term: "Available for this customer", description: availableFact(batch) },
    { term: "Cost", description: customerCost(batch) },
    { term: "Delivery email", description: batch.email },
  ];
}

/** The actions a Batch offers, from the server, plus re-notify (A1). */
export function BatchActions({
  batch,
  onChanged,
}: {
  batch: BatchDetail;
  onChanged: () => void;
}) {
  return (
    <Stack gap={3}>
      <ControlActionBar
        actions={batch.actions}
        resolve={postReason(requestEndpoint(batch.id))}
        settled="Nothing is waiting on this Batch."
        primaryAction="approve"
        onConfirmed={onChanged}
        refresh={onChanged}
      />
      {RENOTIFY_STATUSES.has(batch.status) ? (
        <RenotifyAction requestId={batch.id} onQueued={onChanged} />
      ) : null}
      {batch.telegram.decisionPending ? (
        <Text size="xs" tone="muted">
          Also open in Telegram. Acting here settles it in both places.
        </Text>
      ) : null}
    </Stack>
  );
}

export interface BatchPanelProps {
  /** The Batch number. */
  reference: number;
  /** The small label's record type, e.g. "Approval" on Today. */
  kind?: string;
  /** Shown before the Batch is read, so the heading never flickers empty. */
  fallbackTitle: string;
  onClose: () => void;
  /** Re-read the list beside the panel after an action. */
  onChanged: () => void;
  autoFocus?: boolean | undefined;
  returnFocus?: (() => HTMLElement | null) | undefined;
  /** "customer" when opened on the Customer's own page: the title names the
   *  size instead of the Customer, and the batch's progress is shown. */
  context?: "list" | "customer";
}

/** A Batch opened beside a list: what decides it, and its actions. */
export function BatchPanel({
  reference,
  kind = "Batch",
  fallbackTitle,
  onClose,
  onChanged,
  autoFocus,
  returnFocus,
  context = "list",
}: BatchPanelProps) {
  const { batch, error, reload } = useBatch(reference);
  function changed() {
    reload();
    onChanged();
  }
  const onCustomerPage = context === "customer";
  return (
    <SidePanel
      eyebrow={batch ? `${kind} · ${stageLabel(batch.stage)}` : kind}
      title={
        batch
          ? onCustomerPage
            ? `#${batch.number} · ${formatCount(batch.leadCount)} leads`
            : `#${batch.number} · ${batch.customerIdentity}`
          : fallbackTitle
      }
      onClose={onClose}
      fullHref={`/app/admin/batches/${reference}`}
      autoFocus={autoFocus}
      returnFocus={returnFocus}
      actions={batch ? <BatchActions batch={batch} onChanged={changed} /> : null}
    >
      {batch ? (
        <>
          <FactList
            label={`Batch #${batch.number}`}
            items={onCustomerPage ? customerFacts(batch) : panelFacts(batch)}
          />
          {onCustomerPage && batch.progress?.length ? (
            <Steps
              label={`Batch #${batch.number} progress`}
              steps={batch.progress.map((step) => ({
                key: step.key,
                label: step.label,
                done: step.done,
                time: step.at ? formatAsOf(step.at, new Date()) : null,
              }))}
            />
          ) : null}
        </>
      ) : error ? (
        <Stack gap={2}>
          <Text size="sm" tone="danger" role="alert">
            {error}
          </Text>
          <div>
            <Button onClick={reload}>Try again</Button>
          </div>
        </Stack>
      ) : (
        <Text size="sm" tone="muted" role="status">
          Reading Batch #{reference}…
        </Text>
      )}
      {batch && batch.fill.olderOpen && batch.fill.olderOpen.count > 0 ? (
        <Text size="xs" tone="muted">
          {plural(batch.fill.olderOpen.count, "older open batch", "older open batches")} waiting.
        </Text>
      ) : null}
    </SidePanel>
  );
}
