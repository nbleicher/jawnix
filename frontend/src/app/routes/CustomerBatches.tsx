import { useCallback, useEffect, useRef, useState } from "react";
import {
  useLoaderData,
  useLocation,
  useNavigate,
  useParams,
  useRevalidator,
} from "react-router";

import { ApplicationLink } from "../../design-system/primitives/ApplicationLink";
import { ActionLink, ButtonLink } from "../../design-system/primitives/Button";
import { FactList } from "../../design-system/primitives/facts";
import type { Fact } from "../../design-system/primitives/facts";
import { EmptyState } from "../../design-system/primitives/feedback";
import { Cluster, Page, Section } from "../../design-system/primitives/layout";
import { Panel, RecordColumns, Steps } from "../../design-system/primitives/panel";
import { StatusBadge } from "../../design-system/primitives/status";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { Text } from "../../design-system/primitives/typography";
import { useBilledWallet, useCreditWallet } from "../billing/CreditWalletContext";
import { formatCents } from "../billing/money";
import {
  BatchRowAction,
  CancelBatchButton,
  DownloadButton,
  NewCopyButton,
} from "../customer/BatchActions";
import type { BatchActionResult } from "../customer/BatchActions";
import { batchLabel, batchName } from "../customer/batchLabel";
import {
  batchAction,
  formatBatchCost,
  formatFiles,
  isMoving,
  stageBadge,
} from "../customer/batchStage";
import {
  formatCount,
  formatDay,
  formatRatePerThousand,
  formatStates,
  formatClock,
  plural,
} from "../customer/format";
import { BATCHES_HREF, NEW_REQUEST_HREF } from "../customer/navigation";
import { ReportLeadForm } from "../customer/ReportLeadForm";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { loadBatchRequest } from "./batchRequests";
import type { BatchRequest, BatchRequestWorkspace } from "./batchRequests";
import type { SubmittedState } from "./CustomerNewRequest";
import { useLiveRequests } from "./useLiveRequests";

import "./CustomerBatches.css";

/**
 * Batches (CP3): every batch the Customer has requested, a page at a time,
 * with one action per row; and the batch page with its file, leads per
 * state, charge, history, a new copy of an expired file, and "Report a bad
 * lead". Supply stays hidden: nothing here says what is available.
 */

const REQUEST_EVENTS = "/api/me/batch-requests/events";

/** What a row action just did, said once above the table. */
function ActionNotice({ result }: { result: BatchActionResult | null }) {
  if (!result) return null;
  return result.failed ? (
    <div className="customer-batches-notice customer-batches-notice--failed" role="alert">
      {result.message}
    </div>
  ) : (
    <div className="customer-batches-notice" role="status">
      {result.message}
    </div>
  );
}

function StageBadge({
  batch,
  now,
  withDate = false,
}: {
  batch: BatchRequest;
  now: Date;
  withDate?: boolean;
}) {
  const badge = stageBadge(batch, { now, withDate });
  return <StatusBadge tone={badge.tone}>{badge.label}</StatusBadge>;
}

// --- Batches ------------------------------------------------------------------

function batchesHref(page: number): string {
  return page > 1 ? `${BATCHES_HREF}?page=${page}` : BATCHES_HREF;
}

function BatchPages({ workspace }: { workspace: BatchRequestWorkspace }) {
  const { page, page_size: size, total } = workspace.history;
  const pages = Math.max(1, Math.ceil(total / size));
  const first = (page - 1) * size + 1;
  const last = Math.min(total, page * size);
  return (
    <Cluster gap={3} justify="space-between" className="customer-batches-pages">
      <Text size="sm" tone="muted">
        {`Showing ${formatCount(first)}–${formatCount(last)} of ${formatCount(total)}`}
      </Text>
      {pages > 1 ? (
        <nav aria-label="Batch pages" className="customer-batches-pages__nav">
          {page > 1 ? (
            <ButtonLink variant="secondary" href={batchesHref(page - 1)}>
              Previous
            </ButtonLink>
          ) : null}
          {page < pages ? (
            <ButtonLink variant="secondary" href={batchesHref(page + 1)}>
              Next
            </ButtonLink>
          ) : null}
        </nav>
      ) : null}
    </Cluster>
  );
}

export function CustomerBatchesRoute() {
  const workspace = useLoaderData<BatchRequestWorkspace>();
  const revalidator = useRevalidator();
  const wallet = useBilledWallet();
  const { refresh: refreshWallet } = useCreditWallet();
  const [result, setResult] = useState<BatchActionResult | null>(null);
  const [updated, setUpdated] = useState<Record<string, BatchRequest>>({});
  useDocumentTitle("Batches");
  const now = new Date();

  // A fresh read replaces anything an action patched in.
  useEffect(() => setUpdated({}), [workspace]);
  const batches = workspace.requests.map((batch) => updated[batch.id] ?? batch);

  // Batches that move on their own (approval, preparation, a new copy) are
  // read again when the backend says they changed.
  useLiveRequests(
    REQUEST_EVENTS,
    batches.filter(isMoving).map((batch) => batch.id),
    () => revalidator.revalidate(),
    () => void revalidator.revalidate(),
  );

  function onResult(next: BatchActionResult) {
    setResult(next);
    if (next.batch) {
      const batch = next.batch;
      setUpdated((current) => ({ ...current, [batch.id]: batch }));
      if (batch.stage === "canceled") void refreshWallet();
    }
  }

  const showCost = wallet !== null || batches.some((batch) => batch.cost !== null);
  // Phones stack each row into labelled lines beside its action (V8), so the
  // separate columns step aside there.
  const stacked = (batch: BatchRequest) => {
    const when = batch.delivered_at
      ? `Delivered ${formatDay(batch.delivered_at, now)}`
      : `Submitted ${formatDay(batch.submitted_at, now)}`;
    return (
      <span className="customer-batches-table__stacked">
        <span>{`${plural(batch.lead_count, "lead")} · ${formatStates(batch.states)}`}</span>
        <StageBadge batch={batch} now={now} />
        <span>{showCost ? `${when} · ${formatBatchCost(batch.cost)}` : when}</span>
      </span>
    );
  };
  const columns: DataColumn<BatchRequest>[] = [
    {
      key: "batch",
      header: "Batch",
      cell: (batch) => (
        <>
          <ApplicationLink href={batch.href} aria-label={batchName(batch)}>
            {batchLabel(batch)}
          </ApplicationLink>
          {stacked(batch)}
        </>
      ),
    },
    {
      key: "leads",
      header: "Leads",
      numeric: true,
      hideWhenStacked: true,
      cell: (batch) => formatCount(batch.lead_count),
    },
    {
      key: "states",
      header: "States",
      hideWhenStacked: true,
      cell: (batch) => formatStates(batch.states),
    },
    {
      key: "status",
      header: "Status",
      hideWhenStacked: true,
      cell: (batch) => <StageBadge batch={batch} now={now} />,
    },
    {
      key: "submitted",
      header: "Submitted",
      hideWhenStacked: true,
      cell: (batch) => formatDay(batch.submitted_at, now),
    },
    {
      key: "delivered",
      header: "Delivered",
      hideWhenStacked: true,
      cell: (batch) => (batch.delivered_at ? formatDay(batch.delivered_at, now) : "—"),
    },
    ...(showCost
      ? [
          {
            key: "cost",
            header: "Cost",
            numeric: true,
            hideWhenStacked: true,
            cell: (batch: BatchRequest) => formatBatchCost(batch.cost),
          },
        ]
      : []),
    {
      key: "action",
      header: "Action",
      hideHeader: true,
      cell: (batch) => <BatchRowAction batch={batch} onResult={onResult} />,
    },
  ];

  return (
    <Page
      title="Batches"
      width="data"
      density="data"
      description="Every batch you have requested. Files stay available for 30 days."
      actions={
        <ButtonLink href={NEW_REQUEST_HREF} variant="primary">
          New request
        </ButtonLink>
      }
    >
      <ActionNotice result={result} />
      {workspace.history.total === 0 ? (
        <EmptyState
          title="No batches yet"
          description="Choose how many leads you want and which appointed states. We review each request and email you when your batch is ready."
          action={
            <ButtonLink href={NEW_REQUEST_HREF} variant="secondary">
              Request your first batch
            </ButtonLink>
          }
        />
      ) : (
        <>
          <DataTable
            label="Batches"
            className="customer-batches-table"
            columns={columns}
            rows={batches}
            rowKey={(batch) => batch.id}
            hasActions
          />
          <BatchPages workspace={workspace} />
        </>
      )}
    </Page>
  );
}

// --- The batch page -------------------------------------------------------------

/** "Oct 1 14:20" (V5), local time. */
function formatDayTime(value: string, now: Date): string {
  return `${formatDay(value, now)} ${formatClock(value)}`;
}

/** What happens next for a batch still on its way, or why it stopped. */
function stageNote(batch: BatchRequest): string {
  return batch.stage === "waiting_approval"
    ? "Noah reviews each request. We email you when your batch is ready to download."
    : batch.status.description;
}

function pageDescription(batch: BatchRequest, now: Date): string {
  const when = batch.delivered_at
    ? `delivered ${formatDay(batch.delivered_at, now)}`
    : `submitted ${formatDay(batch.submitted_at, now)}`;
  return `${formatCount(batch.lead_count)} leads · ${formatStates(batch.states)} · ${when}`;
}

function chargeFacts(batch: BatchRequest, now: Date): Fact[] {
  const facts: Fact[] = [];
  const cost = batch.cost;
  if (cost) {
    if (cost.rate_cents_per_thousand !== null) {
      facts.push({ term: "Rate", description: formatRatePerThousand(cost.rate_cents_per_thousand) });
    }
    const amount = cost.amount_cents !== null ? formatCents(cost.amount_cents) : "—";
    if (cost.state === "charged") {
      facts.push({
        term: cost.at ? `Charged ${formatDay(cost.at, now)}` : "Charged",
        description: amount,
      });
    } else if (cost.state === "held") {
      facts.push({ term: "Held until delivery", description: amount });
    } else if (cost.state === "released") {
      facts.push({ term: "Charged", description: formatCents(0) });
      facts.push({
        term: "Hold released",
        description: cost.at ? `${amount} · ${formatDay(cost.at, now)}` : amount,
      });
    } else {
      facts.push({ term: "Cost", description: amount });
    }
  }
  facts.push({ term: "Request submitted", description: formatDayTime(batch.submitted_at, now) });
  if (batch.delivered_at) {
    facts.push({ term: "Delivered", description: formatDayTime(batch.delivered_at, now) });
  }
  return facts;
}

function fileFacts(batch: BatchRequest, now: Date): Fact[] {
  const artifact = batch.artifact;
  const facts: Fact[] = [
    { term: "Format", description: formatFiles(batch.lead_count, batch.rows_per_file) },
  ];
  if (!artifact) {
    // Only a batch still on its way will have a file to keep.
    if (batch.stage === "waiting_approval" || batch.stage === "preparing") {
      facts.push({ term: "Available for", description: "30 days after delivery" });
    }
    return facts;
  }
  if (artifact.columns.length) {
    facts.push({ term: "Columns", description: artifact.columns.join(", ") });
  }
  const expiresAt = artifact.expires_at;
  facts.push({
    term: "Available until",
    description: expiresAt
      ? `${formatDay(expiresAt, now)}${artifact.available ? "" : " (expired)"}`
      : "—",
  });
  if (batch.stage === "copy_on_the_way") {
    facts.push({
      term: "New copy",
      description: "On the way. We email you when it is ready.",
    });
  } else if (batch.can_request_copy) {
    facts.push({
      term: "New copy",
      description: "Same leads, ready in a few minutes, emailed to you",
    });
  }
  return facts;
}

function HeaderAction({
  batch,
  onResult,
}: {
  batch: BatchRequest;
  onResult: (result: BatchActionResult) => void;
}) {
  switch (batchAction(batch)) {
    case "download":
      return <DownloadButton batch={batch} variant="primary" />;
    case "new_copy":
      return <NewCopyButton batch={batch} onResult={onResult} variant="primary" />;
    case "cancel":
      return (
        <CancelBatchButton batch={batch} onResult={onResult} variant="danger" label="Cancel batch" />
      );
    default:
      return null;
  }
}

function SubmittedNotice({ created }: { created: boolean }) {
  return (
    <div className="customer-batches-notice" role="status">
      {created
        ? "Request submitted. Noah reviews each request. We email you when your batch is ready to download."
        : "This request was already submitted, so it was not sent twice."}
    </div>
  );
}

function BatchPage({ initial }: { initial: BatchRequest }) {
  const [batch, setBatch] = useState(initial);
  const [result, setResult] = useState<BatchActionResult | null>(null);
  const revalidator = useRevalidator();
  const navigate = useNavigate();
  const location = useLocation();
  const { batchId } = useParams();
  const { refresh: refreshWallet } = useCreditWallet();
  const revision = useRef(0);
  const now = new Date();
  useDocumentTitle(batchName(batch));

  useEffect(() => {
    revision.current += 1;
    setBatch(initial);
  }, [initial]);

  // A UUID link (emails sent before batch numbers) settles on the number, so
  // the address the Customer shares reads "batches/1042".
  useEffect(() => {
    if (batchId && batchId !== String(initial.number)) {
      navigate(`/batches/${initial.number}`, { replace: true, state: location.state });
    }
  }, [batchId, initial.number, navigate, location.state]);

  const refresh = useCallback(async () => {
    const version = revision.current;
    const next = await loadBatchRequest(String(initial.number));
    if (revision.current === version) setBatch(next);
  }, [initial.number]);

  useLiveRequests(
    REQUEST_EVENTS,
    isMoving(batch) ? [batch.id] : [],
    refresh,
    () => void revalidator.revalidate(),
  );

  function onResult(next: BatchActionResult) {
    setResult(next);
    if (next.batch) {
      revision.current += 1;
      setBatch(next.batch);
      if (next.batch.stage === "canceled") void refreshWallet();
    }
  }

  const submitted = (location.state as SubmittedState | null)?.submitted === true;
  const delivered = batch.delivered_at !== null && batch.stage !== "needs_attention";
  const byState = batch.leads_by_state ?? [];
  const steps = batch.history.map((event, index) => ({
    key: `${event.key}-${index}`,
    label: event.label,
    done: event.done,
    time: event.at ? formatDay(event.at, now) : null,
  }));

  return (
    <Page
      title={batchName(batch)}
      width="data"
      density="data"
      description={pageDescription(batch, now)}
      actions={
        <>
          <StageBadge batch={batch} now={now} withDate />
          <HeaderAction batch={batch} onResult={onResult} />
        </>
      }
    >
      {submitted && !result ? (
        <SubmittedNotice created={(location.state as SubmittedState).created} />
      ) : null}
      <ActionNotice result={result} />
      <RecordColumns
        main={
          <>
            {delivered && byState.length ? (
              <Section title="Leads by state">
                <DataTable
                  label="Leads by state"
                  className="customer-batch-states"
                  columns={[
                    {
                      key: "state",
                      header: "State",
                      cell: (row) => row.name,
                      footer: "Total",
                    },
                    {
                      key: "leads",
                      header: "Leads",
                      numeric: true,
                      cell: (row) => formatCount(row.lead_count),
                      footer: formatCount(byState.reduce((sum, row) => sum + row.lead_count, 0)),
                    },
                  ]}
                  rows={byState}
                  rowKey={(row) => row.state || "unknown"}
                />
              </Section>
            ) : null}
            <Section title="Files">
              <FactList label="Files" items={fileFacts(batch, now)} />
            </Section>
            {delivered ? (
              <Section title="Report a bad lead">
                <ReportLeadForm batch={batch} />
              </Section>
            ) : null}
          </>
        }
        aside={
          <>
            <Panel title={batch.cost ? "Charge" : "Dates"}>
              <FactList label={batch.cost ? "Charge" : "Dates"} items={chargeFacts(batch, now)} />
            </Panel>
            <Panel title="History">
              <Steps label={`History of ${batchName(batch)}`} steps={steps} />
              {!delivered ? (
                <Text size="xs" tone="muted">
                  {stageNote(batch)}
                </Text>
              ) : null}
            </Panel>
            <ActionLink href={BATCHES_HREF}>All batches</ActionLink>
          </>
        }
      />
    </Page>
  );
}

export function CustomerBatchRoute() {
  const batch = useLoaderData<BatchRequest | null>();
  if (!batch) {
    return <BatchNotFound />;
  }
  return <BatchPage initial={batch} />;
}

function BatchNotFound() {
  useDocumentTitle("Batch not found");
  return (
    <Page title="Batch not found" width="data" density="data">
      <EmptyState
        title="This batch is not in your history"
        description="Check the link, or find the batch in your list of batches."
        action={<ActionLink href={BATCHES_HREF}>All batches</ActionLink>}
      />
    </Page>
  );
}
