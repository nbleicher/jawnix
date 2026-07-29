import type { LoaderFunctionArgs } from "react-router";
import { Link, useLoaderData } from "react-router";

import { ActionLink } from "../../design-system/primitives/Button";
import { DetailList } from "../../design-system/primitives/detail";
import { EmptyState } from "../../design-system/primitives/feedback";
import {
  Card,
  Grid,
  Page,
  Section,
  Stack,
} from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import { Heading, Mono, Text } from "../../design-system/primitives/typography";
import { api } from "../auth/adminMFA";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import {
  ActivityTimeline,
  loadEntityActivity,
} from "./AdminActivity";
import type { ActivityPage } from "./AdminActivity";

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

function formatDate(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return `${parsed.toISOString().replace("T", " ").slice(0, 16)} UTC`;
}

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
  useDocumentTitle(`Scraper Configuration · v${item.version}`);
  return (
    <Page
      title={`Scraper Configuration v${item.version}`}
      description="An immutable acquisition configuration and its consequential Activity."
      actions={
        <ActionLink href="/app/admin/acquisition">Back to Acquisition</ActionLink>
      }
    >
      <Stack gap={6}>
        <Section title="Configuration" description="Version identity and lifecycle.">
          <Card>
            <DetailList
              label="Scraper Configuration"
              items={[
                {
                  term: "Status",
                  description: (
                    <StatusBadge>{item.status.replaceAll("_", " ")}</StatusBadge>
                  ),
                },
                { term: "Reason", description: item.reason },
                { term: "Created by", description: <Mono>{item.createdBy}</Mono> },
                { term: "Created", description: formatDate(item.createdAt) },
                { term: "Scheduled", description: formatDate(item.scheduledAt) },
                { term: "Activated", description: formatDate(item.activatedAt) },
                { term: "Checksum", description: <Mono>{item.checksum}</Mono> },
                {
                  term: "Based on",
                  description: item.basedOnConfigurationId ? (
                    <Link
                      to={`/admin/acquisition/configurations/${item.basedOnConfigurationId}`}
                    >
                      Earlier Scraper Configuration
                    </Link>
                  ) : (
                    "Original version"
                  ),
                },
              ]}
            />
          </Card>
        </Section>

        <Section
          title="Source Segments"
          description="Queries and geography preserved with this version."
        >
          {item.segments.length ? (
            <Grid minColumnWidth="18rem">
              {item.segments.map((segment) => (
                <Card as="article" key={segment.key}>
                  <Stack gap={2}>
                    <Heading level={3} size="sm">
                      {segment.key}
                    </Heading>
                    <Text size="sm">{segment.niche}</Text>
                    <Text size="sm" tone="muted">
                      {segment.query} · {segment.geography}
                    </Text>
                  </Stack>
                </Card>
              ))}
            </Grid>
          ) : (
            <EmptyState
              title="No Source Segments"
              description="This version contains no acquisition queries."
            />
          )}
        </Section>

        <Section
          title="Activity"
          description="Consequential changes to this Scraper Configuration, most recent first."
        >
          <ActivityTimeline
            activity={item.activityTimeline}
            emptyDescription="No consequential action has been recorded for this Scraper Configuration."
          />
        </Section>
      </Stack>
    </Page>
  );
}

export function ScrapeRunDetailsRoute() {
  const item = useLoaderData<RunDetail>();
  useDocumentTitle(`Scrape Run · ${item.id}`);
  return (
    <Page
      title={`Scrape Run ${item.id}`}
      description="One acquisition run, its segment results, and its read-only Activity."
      actions={
        <ActionLink href="/app/admin/acquisition">Back to Acquisition</ActionLink>
      }
    >
      <Stack gap={6}>
        <Section title="Run" description="Execution identity and result.">
          <Card>
            <DetailList
              label="Scrape Run"
              items={[
                {
                  term: "Status",
                  description: <StatusBadge>{item.status}</StatusBadge>,
                },
                {
                  term: "Configuration",
                  description: item.configurationId ? (
                    <Link
                      to={`/admin/acquisition/configurations/${item.configurationId}`}
                    >
                      View Scraper Configuration
                    </Link>
                  ) : (
                    "No configuration linked"
                  ),
                },
                { term: "Source", description: item.source },
                { term: "Source version", description: item.sourceVersion },
                { term: "Manual", description: item.manual ? "Yes" : "No" },
                {
                  term: "Rows",
                  description: `${item.rowsImported.toLocaleString()} imported of ${item.rowsSeen.toLocaleString()} seen`,
                },
                {
                  term: "Dataset version",
                  description: item.datasetVersion ?? "Not published",
                },
                { term: "Started", description: formatDate(item.startedAt) },
                { term: "Finished", description: formatDate(item.finishedAt) },
                {
                  term: "Checksum",
                  description: item.checksum ? <Mono>{item.checksum}</Mono> : "—",
                },
              ]}
            />
          </Card>
        </Section>

        <Section title="Segment results" description="Counts and anomaly evidence.">
          {item.segments.length ? (
            <Grid minColumnWidth="18rem">
              {item.segments.map((segment) => (
                <Card as="article" key={segment.key}>
                  <Stack gap={2}>
                    <Heading level={3} size="sm">
                      {segment.key}
                    </Heading>
                    <Text size="sm">
                      {segment.valid.toLocaleString()} valid ·{" "}
                      {segment.new.toLocaleString()} new
                    </Text>
                    <Text size="sm" tone={segment.anomalous ? "warning" : "muted"}>
                      {segment.anomalous
                        ? segment.anomalyReasons.join(", ").replaceAll("_", " ")
                        : "No anomaly recorded"}
                    </Text>
                  </Stack>
                </Card>
              ))}
            </Grid>
          ) : (
            <EmptyState
              title="No segment results"
              description="This Scrape Run has no recorded Source Segment output."
            />
          )}
        </Section>

        <Section
          title="Activity"
          description="Consequential decisions on this Scrape Run, most recent first."
        >
          <ActivityTimeline
            activity={item.activityTimeline}
            emptyDescription="No consequential action has been recorded for this Scrape Run."
          />
        </Section>
      </Stack>
    </Page>
  );
}
