import { useLoaderData, useRevalidator } from "react-router";

import { ActionLink } from "../../design-system/primitives/Button";
import { ErrorState } from "../../design-system/primitives/feedback";
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
import { Heading, Text } from "../../design-system/primitives/typography";
import { api } from "../auth/adminMFA";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import "./AdminDestinations.css";

interface OperationAction {
  label: string;
  href: string;
}

interface OperationItem {
  id: string;
  title: string;
  summary: string;
  status: string;
  tone: StatusTone;
  nextAction: string;
  recordedAt: string;
  action: OperationAction;
}

interface OperationQueue {
  key: string;
  title: string;
  description: string;
  count: number;
  items: OperationItem[];
  emptyTitle: string;
  emptyDescription: string;
}

interface OperationSource {
  key: string;
  title: string;
  description: string;
  status: "available" | "unavailable";
  count: number | null;
  queues: OperationQueue[];
  workspace: OperationAction;
  errorTitle: string | null;
  errorDescription: string | null;
}

export interface OperationsOverviewData {
  generatedAt: string;
  availableCount: number;
  degraded: boolean;
  sources: OperationSource[];
}

export async function adminOverviewLoader(): Promise<OperationsOverviewData> {
  return api<OperationsOverviewData>("/api/admin/operations-overview");
}

function formatDate(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return `${parsed.toISOString().replace("T", " ").slice(0, 16)} UTC`;
}

function OperationCard({ item }: { item: OperationItem }) {
  return (
    <Card as="article" aria-label={item.title}>
      <Stack gap={3}>
        <Cluster gap={2} justify="space-between">
          <Heading level={4} size="sm">
            {item.title}
          </Heading>
          <StatusBadge tone={item.tone}>{item.status}</StatusBadge>
        </Cluster>
        <Text size="sm">{item.summary}</Text>
        <Text size="sm" tone="muted">
          Next: {item.nextAction}
        </Text>
        <Text size="xs" tone="muted">
          Recorded {formatDate(item.recordedAt)}
        </Text>
        <div>
          <ActionLink href={item.action.href}>
            {item.action.label}
          </ActionLink>
        </div>
      </Stack>
    </Card>
  );
}

function Queue({ queue }: { queue: OperationQueue }) {
  return (
    <section className="jx-operations-queue" aria-label={queue.title}>
      <Stack gap={3}>
        <Cluster gap={2} justify="space-between">
          <Heading level={3}>{queue.title}</Heading>
          <StatusBadge tone={queue.count ? "warning" : "success"}>
            {queue.count.toLocaleString()} pending
          </StatusBadge>
        </Cluster>
        <Text size="sm" tone="muted">
          {queue.description}
        </Text>
        {queue.items.length ? (
          <Grid minColumnWidth="18rem">
            {queue.items.map((item) => (
              <OperationCard item={item} key={item.id} />
            ))}
          </Grid>
        ) : (
          <Card padding={4}>
            <Stack gap={1}>
              <Heading level={4} size="sm">
                {queue.emptyTitle}
              </Heading>
              <Text size="sm" tone="muted">
                {queue.emptyDescription}
              </Text>
            </Stack>
          </Card>
        )}
        {queue.count > queue.items.length ? (
          <Text size="sm" tone="muted">
            Showing {queue.items.length.toLocaleString()} of{" "}
            {queue.count.toLocaleString()} pending items. Open the workspace
            for the complete queue.
          </Text>
        ) : null}
      </Stack>
    </section>
  );
}

function Source({ source }: { source: OperationSource }) {
  const revalidator = useRevalidator();
  return (
    <Section
      title={source.title}
      description={source.description}
    >
      {source.status === "unavailable" ? (
        <Card>
          <Stack gap={3}>
            <ErrorState
              title={
                source.errorTitle ??
                `${source.title} work is temporarily unavailable`
              }
              description={
                source.errorDescription ??
                "This section could not be refreshed. Other Operations sections remain usable."
              }
              retryLabel={`Retry ${source.title}`}
              onRetry={() => revalidator.revalidate()}
            />
            <div>
              <ActionLink href="/app/admin/activity" variant="ghost">
                Review Activity
              </ActionLink>
            </div>
          </Stack>
        </Card>
      ) : (
        <Stack gap={5}>
          {source.queues.map((queue) => (
            <Queue queue={queue} key={queue.key} />
          ))}
          <div>
            <ActionLink href={source.workspace.href} variant="ghost">
              {source.workspace.label}
            </ActionLink>
          </div>
        </Stack>
      )}
    </Section>
  );
}

export function AdminDestinationRoute() {
  const data = useLoaderData<OperationsOverviewData>();
  const unavailable = data.sources.filter(
    (source) => source.status === "unavailable",
  ).length;
  useDocumentTitle("Overview");

  return (
    <Page
      title="Overview"
      description={
        "One actionable queue across Fulfillment, background work, and " +
        "Acquisition. Each item opens the affected record or the workspace " +
        "that owns its next valid action."
      }
      actions={
        <ActionLink href="/app/admin/activity" variant="ghost">
          Investigate Activity
        </ActionLink>
      }
    >
      <Stack gap={6}>
        <Card padding={4}>
          <Cluster gap={3} justify="space-between">
            <Text role="status" aria-live="polite" weight="semibold">
              {data.availableCount.toLocaleString()} pending operation
              {data.availableCount === 1 ? "" : "s"} identified
            </Text>
            <StatusBadge tone={data.degraded ? "warning" : "success"}>
              {data.degraded
                ? `${unavailable} source unavailable`
                : "All sources available"}
            </StatusBadge>
          </Cluster>
        </Card>

        {data.sources.map((source) => (
          <Source source={source} key={source.key} />
        ))}
      </Stack>
    </Page>
  );
}
