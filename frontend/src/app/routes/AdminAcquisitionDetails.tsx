import type { LoaderFunctionArgs } from "react-router";
import { Link, useLoaderData } from "react-router";

import { ActionLink } from "../../design-system/primitives/Button";
import { FactList } from "../../design-system/primitives/facts";
import { Page, Section } from "../../design-system/primitives/layout";
import { Panel, RecordColumns } from "../../design-system/primitives/panel";
import { StatusBadge } from "../../design-system/primitives/status";
import type { StatusTone } from "../../design-system/primitives/status";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { Mono, Text } from "../../design-system/primitives/typography";
import { api } from "../auth/adminMFA";
import { formatCount, formatDateTime } from "../operator/format";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { ActivityTable, loadEntityActivity, recordActivityHref } from "./AdminActivity";
import type { ActivityPage } from "./AdminActivity";

/**
 * A Scraper Configuration version and a Scrape Run, each with its segments
 * and Activity. Linked from Setup's Configuration versions, Today, Insights'
 * Nightly reviews, and Telegram, so their addresses stay where they were.
 */

interface ConfigurationSegment {
  key: string;
  niche: string;
  query: string;
  geography: string;
  parameters: Record<string, unknown>;
}

interface ConfigurationDetail {
  id: string;
  version: number;
  checksum: string;
  status: string;
  createdBy: string;
  reason: string;
  anomalyThresholds: Record<string, unknown>;
  createdAt: string;
  scheduledAt: string | null;
  activatedAt: string | null;
  basedOnConfigurationId: string | null;
  segments: ConfigurationSegment[];
  activityTimeline: ActivityPage;
}

interface RunSegment {
  key: string;
  niche: string;
  geography: string;
  observed: number;
  valid: number;
  new: number;
  duplicates: number;
  quarantined: number;
  anomalous: boolean;
  anomalyReasons: string[];
}

interface RunDetail {
  id: number;
  source: string;
  sourceVersion: string;
  configurationId: string | null;
  datasetVersion: number | null;
  manual: boolean;
  checksum: string;
  status: string;
  rowsSeen: number;
  rowsImported: number;
  details: Record<string, unknown>;
  startedAt: string;
  finishedAt: string | null;
  segments: RunSegment[];
  activityTimeline: ActivityPage;
}

function words(value: string): string {
  const text = value.replaceAll("_", " ").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "Unknown";
}

// Black is in force or done; red failed or held; grey is history (V4).
const STATUS_TONES: Record<string, StatusTone> = {
  active: "success",
  scheduled: "info",
  restored: "info",
  committed: "success",
  complete: "success",
  completed: "success",
  running: "info",
  staged: "info",
  failed: "danger",
  held: "danger",
  held_anomaly: "danger",
};

function statusBadge(status: string) {
  return <StatusBadge tone={STATUS_TONES[status] ?? "neutral"}>{words(status)}</StatusBadge>;
}

const SEGMENT_COLUMNS: DataColumn<ConfigurationSegment>[] = [
  { key: "query", header: "Keyword", cell: (segment) => segment.query || segment.key },
  { key: "geography", header: "Geography", cell: (segment) => segment.geography },
  { key: "niche", header: "Niche", cell: (segment) => segment.niche || "—" },
];

const RESULT_COLUMNS: DataColumn<RunSegment>[] = [
  { key: "segment", header: "Segment", cell: (segment) => segment.key },
  { key: "valid", header: "Valid", numeric: true, cell: (segment) => formatCount(segment.valid) },
  { key: "new", header: "New", numeric: true, cell: (segment) => formatCount(segment.new) },
  {
    key: "duplicates",
    header: "Duplicates",
    numeric: true,
    cell: (segment) => formatCount(segment.duplicates),
  },
  {
    key: "quarantined",
    header: "Quarantined",
    numeric: true,
    cell: (segment) => formatCount(segment.quarantined),
  },
  {
    key: "anomaly",
    header: "Anomaly",
    cell: (segment) =>
      segment.anomalous ? (
        <Text as="span" size="sm" tone="danger">
          {segment.anomalyReasons.map(words).join(", ") || "Flagged"}
        </Text>
      ) : (
        "None"
      ),
  },
];

export async function scraperConfigurationDetailsLoader({
  params,
}: LoaderFunctionArgs): Promise<ConfigurationDetail> {
  const [configuration, activityTimeline] = await Promise.all([
    api<Omit<ConfigurationDetail, "activityTimeline">>(
      `/api/admin/scraper-configurations/${params.configurationId}`,
    ),
    loadEntityActivity("scraper_configuration", params.configurationId),
  ]);
  return { ...configuration, activityTimeline };
}

export async function scrapeRunDetailsLoader({
  params,
}: LoaderFunctionArgs): Promise<RunDetail> {
  const [run, activityTimeline] = await Promise.all([
    api<Omit<RunDetail, "activityTimeline">>(
      `/api/admin/scrape-runs/${params.runId}`,
    ),
    loadEntityActivity("scrape_run", params.runId),
  ]);
  return { ...run, activityTimeline };
}

export function ScraperConfigurationDetailsRoute() {
  const item = useLoaderData<ConfigurationDetail>();
  const now = new Date();
  useDocumentTitle(`Configuration v${item.version}`);
  return (
    <Page
      title={`Configuration v${item.version}`}
      width="data"
      density="data"
      description="An immutable Scraper Configuration version: its Source Segments and Activity."
      actions={
        <ActionLink href="/app/admin/setup/configurations">All versions</ActionLink>
      }
    >
      <RecordColumns
        main={
          <>
            <Section title={`Source segments · ${formatCount(item.segments.length)}`}>
              <DataTable
                label="Source segments"
                columns={SEGMENT_COLUMNS}
                rows={item.segments}
                rowKey={(segment) => segment.key}
                empty="This version contains no acquisition queries."
              />
            </Section>
            <Section title="Activity">
              <ActivityTable
                activity={item.activityTimeline}
                label="Configuration activity"
                showRecord={false}
                moreHref={recordActivityHref("scraper_configuration", item.id)}
                empty="No consequential action has been recorded for this Scraper Configuration."
              />
            </Section>
          </>
        }
        aside={
          <Panel title="Version">
            <FactList
              label="Scraper Configuration"
              items={[
                { term: "Status", description: statusBadge(item.status) },
                { term: "Reason", description: item.reason },
                { term: "Created", description: formatDateTime(item.createdAt, now) },
                { term: "Scheduled", description: formatDateTime(item.scheduledAt, now) },
                { term: "Activated", description: formatDateTime(item.activatedAt, now) },
                {
                  term: "Based on",
                  description: item.basedOnConfigurationId ? (
                    <Link to={`/admin/acquisition/configurations/${item.basedOnConfigurationId}`}>
                      Earlier version
                    </Link>
                  ) : (
                    "Original version"
                  ),
                },
                { term: "Checksum", description: <Mono>{item.checksum.slice(0, 12)}</Mono> },
              ]}
            />
          </Panel>
        }
      />
    </Page>
  );
}

export function ScrapeRunDetailsRoute() {
  const item = useLoaderData<RunDetail>();
  const now = new Date();
  useDocumentTitle(`Run ${item.id}`);
  const held = item.segments.filter((segment) => segment.anomalous).length;
  return (
    <Page
      title={`Scrape run ${item.id}`}
      width="data"
      density="data"
      description="One acquisition run: what each Source Segment returned, and its Activity."
      actions={<ActionLink href="/app/admin/insights#nightly-reviews">Nightly reviews</ActionLink>}
    >
      <RecordColumns
        main={
          <>
            <Section
              title={`Segment results · ${formatCount(item.segments.length)}`}
              {...(held ? { description: `${formatCount(held)} flagged as anomalous.` } : {})}
            >
              <DataTable
                label="Segment results"
                columns={RESULT_COLUMNS}
                rows={item.segments}
                rowKey={(segment) => segment.key}
                empty="This Scrape Run has no recorded Source Segment output."
              />
            </Section>
            <Section title="Activity">
              <ActivityTable
                activity={item.activityTimeline}
                label="Scrape run activity"
                showRecord={false}
                moreHref={recordActivityHref("scrape_run", item.id)}
                empty="No consequential action has been recorded for this Scrape Run."
              />
            </Section>
          </>
        }
        aside={
          <Panel title="Run">
            <FactList
              label="Scrape Run"
              items={[
                { term: "Status", description: statusBadge(item.status) },
                {
                  term: "Configuration",
                  description: item.configurationId ? (
                    <Link to={`/admin/acquisition/configurations/${item.configurationId}`}>
                      View version
                    </Link>
                  ) : (
                    "None linked"
                  ),
                },
                { term: "Source", description: item.source },
                { term: "Manual", description: item.manual ? "Yes" : "No" },
                {
                  term: "Rows",
                  description: `${formatCount(item.rowsImported)} of ${formatCount(item.rowsSeen)}`,
                },
                { term: "Dataset", description: item.datasetVersion ? `v${item.datasetVersion}` : "Not published" },
                { term: "Started", description: formatDateTime(item.startedAt, now) },
                { term: "Finished", description: formatDateTime(item.finishedAt, now) },
              ]}
            />
          </Panel>
        }
      />
    </Page>
  );
}
