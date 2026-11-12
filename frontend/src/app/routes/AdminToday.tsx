import { Fragment, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Link, useLoaderData, useRevalidator } from "react-router";

import { ActionLink, Button } from "../../design-system/primitives/Button";
import { EmptyState } from "../../design-system/primitives/feedback";
import { Cluster, Page, Section } from "../../design-system/primitives/layout";
import { PanelLayout } from "../../design-system/primitives/panel";
import { StatusBadge } from "../../design-system/primitives/status";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { Text } from "../../design-system/primitives/typography";
import { api } from "../api";
import { routePath } from "../routePath";
import { DecisionPanel } from "../operator/DecisionPanel";
import type { Decision } from "../operator/DecisionPanel";
import {
  formatAge,
  formatAgo,
  formatCount,
  formatTime,
  plural,
  stageLabel,
  stageTone,
} from "../operator/format";
import type { BatchRow } from "../operator/format";
import { useSelectedRow } from "../operator/useSelectedRow";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { formatAsOf, OperatorNumbers } from "./OperatorNumbers";
import type { OperatorNumbersData } from "./OperatorNumbers";
import "./AdminToday.css";

/**
 * Today (P1): the operator's daily page. Numbers first, then last night's
 * run beside the Scraper's health, then every decision waiting in one
 * oldest-first list whose rows open beside it, then the batches moving on
 * their own.
 */

export interface LastNight {
  run: { id: number; committedAt: string; syncStatus: string; href: string } | null;
  newLeads: number | null;
  heldSegments: number;
  review: {
    id: string;
    reviewDate: string | null;
    status: string;
    telegram: string;
    telegramAt: string | null;
  } | null;
}

/** GET /api/admin/scraper/health: only a sign-in holding the Scraper grant
 *  gets the numbers; without one the Scraper was never asked. */
export type ScraperHealth =
  | { granted: false }
  | {
      granted: true;
      serviceState: "connected" | "unavailable";
      checkedAt: string;
      pipeline: { key: "pausing" | "paused" | "running" | "stopped"; label: string } | null;
      workersRunning: number | null;
      workersTotal: number | null;
      queueDepth: number | null;
      latestWriteAt: string | null;
      writeIsFresh: boolean | null;
      lastSuccessfulAt: string | null;
    };

export interface TodayData {
  generatedAt: string;
  numbers: OperatorNumbersData | null;
  lastNight: LastNight | null;
  decisions: { items: Decision[]; total: number; unavailable: string[] };
  batches: {
    inProgress: BatchRow[];
    deliveredToday: BatchRow[];
    allBatches: number;
  } | null;
  /** Read beside Today, under the Scraper's path so the grant cookie goes
   *  with it. Null when the check itself failed. */
  scraper: ScraperHealth | null;
}

/** Where the Scraper is entered: a live grant goes straight through. */
const SCRAPER_ENTRY = "/admin/acquisition/scraper";

function localMidnight(now: Date): Date {
  const midnight = new Date(now);
  midnight.setHours(0, 0, 0, 0);
  return midnight;
}

export async function adminTodayLoader(): Promise<TodayData> {
  const since = localMidnight(new Date()).toISOString();
  const [today, scraper] = await Promise.all([
    api<Omit<TodayData, "scraper">>(`/api/admin/today?deliveredSince=${encodeURIComponent(since)}`),
    api<ScraperHealth>("/api/admin/scraper/health").catch(() => null),
  ]);
  return { ...today, scraper };
}

function describeToday(now: Date): string {
  const weekday = now.toLocaleString("en-US", { weekday: "long" });
  const month = now.toLocaleString("en-US", { month: "short" });
  return `${weekday}, ${month} ${now.getDate()}. Work top to bottom; each row opens beside the list.`;
}

const UNAVAILABLE_LABELS: Record<string, string> = {
  batches: "batches",
  inventoryConflicts: "inventory conflicts",
  leadReports: "lead reports",
  failedJobs: "failed jobs",
  scrapeAnomalies: "scrape anomalies",
  nicheMappings: "niche mappings",
  exclusionLists: "exclusion lists",
  registrations: "registrations",
};

function telegramText(review: NonNullable<LastNight["review"]>, now: Date) {
  switch (review.telegram) {
    case "sent":
      return review.telegramAt
        ? `Telegram sent ${formatAsOf(review.telegramAt, now)}`
        : "Telegram sent";
    case "pending":
    case "sending":
      return "Telegram sending";
    case "unknown":
      return <Text as="span" size="sm" tone="danger">Telegram delivery unknown</Text>;
    default:
      return <Text as="span" size="sm" tone="danger">Telegram not sent</Text>;
  }
}

/** "running · 6 of 8 workers · queue 812 · last write just now". */
function ScraperHealthLine({ health, now }: { health: ScraperHealth | null; now: Date }) {
  const label = (
    <Link to={SCRAPER_ENTRY}>
      <strong>Scraper</strong>
    </Link>
  );
  if (health === null) {
    return (
      <section className="jx-today-line__part jx-today-line__scraper" aria-label="Scraper health">
        <span>
          {label}{" "}
          <Text as="span" size="sm" tone="muted">health could not be checked</Text>
        </span>
      </section>
    );
  }
  if (!health.granted) {
    return (
      <section className="jx-today-line__part jx-today-line__scraper" aria-label="Scraper health">
        <Link to={SCRAPER_ENTRY}>Scraper · verify to see health</Link>
      </section>
    );
  }
  if (health.serviceState === "unavailable") {
    return (
      <section className="jx-today-line__part jx-today-line__scraper" aria-label="Scraper health">
        <span>
          {label}{" "}
          <Text as="span" size="sm" tone="danger">
            unavailable
            {health.lastSuccessfulAt ? ` · last reached ${formatAsOf(health.lastSuccessfulAt, now)}` : ""}
          </Text>
        </span>
      </section>
    );
  }
  const parts: ReactNode[] = [];
  if (health.pipeline) {
    parts.push(
      health.pipeline.key === "stopped" ? (
        <Text as="span" size="sm" tone="danger">{health.pipeline.label.toLowerCase()}</Text>
      ) : (
        health.pipeline.label.toLowerCase()
      ),
    );
  }
  if (health.workersRunning !== null) {
    parts.push(
      health.workersTotal !== null
        ? `${formatCount(health.workersRunning)} of ${plural(health.workersTotal, "worker")}`
        : `${plural(health.workersRunning, "worker")} running`,
    );
  } else if (health.workersTotal !== null) {
    parts.push(`${plural(health.workersTotal, "worker")} expected`);
  }
  if (health.queueDepth !== null) parts.push(`queue ${formatCount(health.queueDepth)}`);
  if (health.pipeline || health.latestWriteAt) {
    const write = health.latestWriteAt
      ? `last write ${formatAgo(health.latestWriteAt, now)}`
      : "no writes yet";
    // A running pipeline that has stopped writing is the thing to notice.
    parts.push(
      health.pipeline?.key === "running" && health.writeIsFresh === false ? (
        <Text as="span" size="sm" tone="danger">{write}</Text>
      ) : (
        write
      ),
    );
  }
  return (
    <section className="jx-today-line__part jx-today-line__scraper" aria-label="Scraper health">
      <span>
        {label}{" "}
        {parts.map((part, index) => (
          <Fragment key={index}>
            {index ? " · " : ""}
            {part}
          </Fragment>
        ))}
      </span>
    </section>
  );
}

/** Last night's run and the Scraper's health in one ruled line. */
function TodayLine({
  lastNight,
  scraper,
  now,
}: {
  lastNight: LastNight | null;
  scraper: ScraperHealth | null;
  now: Date;
}) {
  return (
    <div className="jx-today-line">
      <LastNightLine lastNight={lastNight} now={now} />
      <ScraperHealthLine health={scraper} now={now} />
    </div>
  );
}

function LastNightLine({ lastNight, now }: { lastNight: LastNight | null; now: Date }) {
  if (!lastNight) {
    return (
      <section className="jx-today-line__part" aria-label="Last night">
        <strong>Last night</strong>
        <span>No Scrape Run has been committed yet.</span>
      </section>
    );
  }
  const { run, review } = lastNight;
  return (
    <section className="jx-today-line__part" aria-label="Last night">
      <strong>Last night</strong>
      {run ? (
        <span>
          <Link to={routePath(run.href)}>Run {run.id}</Link> committed{" "}
          {formatAsOf(run.committedAt, now)}
        </span>
      ) : (
        <span>No run committed</span>
      )}
      {lastNight.newLeads !== null ? (
        <span>+{formatCount(lastNight.newLeads)} new leads</span>
      ) : null}
      {lastNight.heldSegments > 0 ? (
        <Text as="span" size="sm" tone="danger">
          {plural(lastNight.heldSegments, "segment")} held
        </Text>
      ) : (
        <span>No segments held</span>
      )}
      {review && review.status === "waiting_publication" ? (
        <span>Nightly review waiting for publication</span>
      ) : null}
      {review ? <span>{telegramText(review, now)}</span> : null}
    </section>
  );
}

function BatchStatus({ row }: { row: BatchRow }) {
  const shortBy = row.availability.kind === "short" ? row.availability.short ?? 0 : 0;
  return (
    <Cluster gap={2}>
      <StatusBadge tone={stageTone(row.stage)}>{stageLabel(row.stage)}</StatusBadge>
      {shortBy > 0 ? (
        <Text as="span" size="sm" tone="muted">
          short {formatCount(shortBy)}
        </Text>
      ) : null}
    </Cluster>
  );
}

function batchLink(row: BatchRow) {
  return <Link to={routePath(row.href)}>#{row.number}</Link>;
}

const IN_PROGRESS_COLUMNS: DataColumn<BatchRow>[] = [
  { key: "batch", header: "Batch", cell: batchLink },
  { key: "customer", header: "Customer", cell: (row) => row.customer.name },
  { key: "leads", header: "Leads", numeric: true, cell: (row) => formatCount(row.leadCount) },
  { key: "status", header: "Status", cell: (row) => <BatchStatus row={row} /> },
  { key: "age", header: "Age", cell: (row) => formatAge(row.createdAt) },
];

const DELIVERED_COLUMNS: DataColumn<BatchRow>[] = [
  { key: "batch", header: "Batch", cell: batchLink },
  { key: "customer", header: "Customer", cell: (row) => row.customer.name },
  { key: "leads", header: "Leads", numeric: true, cell: (row) => formatCount(row.leadCount) },
  {
    key: "status",
    header: "Status",
    cell: (row) => <StatusBadge tone={stageTone(row.stage)}>{stageLabel(row.stage)}</StatusBadge>,
  },
  { key: "time", header: "Time", cell: (row) => formatTime(row.deliveredAt ?? row.fileAt) },
];

export function AdminTodayRoute() {
  const data = useLoaderData<TodayData>();
  const revalidator = useRevalidator();
  const now = new Date();
  useDocumentTitle("Today");
  const selection = useSelectedRow("decision");
  const items = data.decisions.items;
  const current = items.find((item) => item.key === selection.selected) ?? null;
  // A decision taken in the panel leaves the list; the panel stays open on
  // what was decided until the operator closes it.
  const [lastOpened, setLastOpened] = useState<Decision | null>(null);
  useEffect(() => {
    if (current) setLastOpened(current);
  }, [current]);
  const shown = current ?? (lastOpened?.key === selection.selected ? lastOpened : null);

  const decisionColumns: DataColumn<Decision>[] = [
    {
      key: "type",
      header: "Type",
      cell: (item) => (
        <Link
          to={item.href ? routePath(item.href) : "."}
          data-panel-trigger={item.key}
          onClick={selection.openFromLink(item.key)}
        >
          {item.label}
        </Link>
      ),
    },
    { key: "subject", header: "Subject", cell: (item) => item.subject },
    { key: "detail", header: "Detail", cell: (item) => item.detail },
    { key: "waiting", header: "Waiting", cell: (item) => formatAge(item.since) },
  ];

  const batches = data.batches;
  const unavailable = data.decisions.unavailable;

  return (
    <Page title="Today" width="data" density="data" description={describeToday(now)}>
      {data.numbers ? (
        <OperatorNumbers numbers={data.numbers} now={now} />
      ) : (
        <Text size="sm" tone="muted" role="status">
          The numbers are temporarily unavailable. The decisions below are current.
        </Text>
      )}
      <TodayLine lastNight={data.lastNight} scraper={data.scraper} now={now} />
      <PanelLayout
        panel={
          shown ? (
            <DecisionPanel
              key={shown.key}
              decision={shown}
              settled={!current}
              onClose={selection.close}
              onChanged={() => void revalidator.revalidate()}
              autoFocus={selection.autoFocus}
              returnFocus={selection.returnFocus(shown.key)}
            />
          ) : null
        }
      >
        <Section title="Decisions">
          {unavailable.length ? (
            <Cluster gap={3}>
              <Text size="sm" tone="danger" role="alert">
                Some decisions could not be read:{" "}
                {unavailable.map((key) => UNAVAILABLE_LABELS[key] ?? key).join(", ")}.
              </Text>
              <Button variant="ghost" onClick={() => void revalidator.revalidate()}>
                Retry
              </Button>
            </Cluster>
          ) : null}
          {items.length ? (
            <DataTable
              label="Decisions"
              columns={decisionColumns}
              rows={items}
              rowKey={(item) => item.key}
              selectedKey={shown?.key ?? null}
              onRowClick={(item) => selection.select(item.key)}
            />
          ) : (
            <EmptyState
              title="Nothing is waiting on you"
              description="Approvals, failures, conflicts, reports, and acquisition reviews appear here, oldest first."
            />
          )}
          {data.decisions.total > items.length ? (
            <Text size="sm" tone="muted">
              Showing the oldest {formatCount(items.length)} of{" "}
              {formatCount(data.decisions.total)} decisions.
            </Text>
          ) : null}
        </Section>

        {batches ? (
          <>
            <Section title="Batches in progress">
              {batches.inProgress.length ? (
                <DataTable
                  label="Batches in progress"
                  columns={IN_PROGRESS_COLUMNS}
                  rows={batches.inProgress}
                  rowKey={(row) => row.id}
                />
              ) : (
                <Text size="sm" tone="muted">
                  No batch is approved, generating, or waiting for inventory.
                </Text>
              )}
            </Section>
            <Section title="Delivered today">
              {batches.deliveredToday.length ? (
                <DataTable
                  label="Delivered today"
                  columns={DELIVERED_COLUMNS}
                  rows={batches.deliveredToday}
                  rowKey={(row) => row.id}
                />
              ) : (
                <Text size="sm" tone="muted">
                  Nothing has been delivered yet today.
                </Text>
              )}
            </Section>
            <div>
              <ActionLink href="/app/admin/batches">
                All batches ({formatCount(batches.allBatches)})
              </ActionLink>
            </div>
          </>
        ) : (
          <Text size="sm" tone="muted" role="status">
            Batches are temporarily unavailable.{" "}
            <ActionLink href="/app/admin/batches">Open All batches</ActionLink>
          </Text>
        )}
      </PanelLayout>
    </Page>
  );
}
