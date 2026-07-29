import type { ReactNode } from "react";
import type { LoaderFunctionArgs } from "react-router";
import { Form, Link, useLoaderData, useLocation } from "react-router";

import { ActionLink, Button } from "../../design-system/primitives/Button";
import { DetailList } from "../../design-system/primitives/detail";
import { EmptyState } from "../../design-system/primitives/feedback";
import { Field, Input, Select } from "../../design-system/primitives/form";
import {
  Card,
  Cluster,
  Grid,
  Page,
  Section,
  Stack,
} from "../../design-system/primitives/layout";
import { Heading, Mono, Text } from "../../design-system/primitives/typography";
import { api } from "../auth/adminMFA";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import "./AdminActivity.css";

export interface ActivityEntry {
  id: string;
  action: string;
  entityType: string;
  entityId: string;
  entityHref: string;
  actor: string;
  reason: string;
  details: Record<string, unknown>;
  recordedAt: string;
}

export interface ActivityPage {
  entries: ActivityEntry[];
  page: number;
  pageSize: number;
  total: number;
  pages: number;
}

const ENTITY_TYPES = [
  ["", "Every entity type"],
  ["agency", "Agency"],
  ["batch_request", "Batch Request"],
  ["customer", "Customer"],
  ["lead_report", "Lead Report"],
  ["scraper_configuration", "Scraper Configuration"],
  ["scrape_run", "Scrape Run"],
] as const;

function words(value: string): string {
  const normalized = value.replaceAll("_", " ").trim();
  return normalized
    ? normalized.charAt(0).toUpperCase() + normalized.slice(1)
    : "Unknown";
}

function formatDate(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return `${parsed.toISOString().replace("T", " ").slice(0, 16)} UTC`;
}

function displayValue(value: unknown): ReactNode {
  if (value === null || value === undefined || value === "") return "None";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "object") {
    return <Mono>{JSON.stringify(value)}</Mono>;
  }
  return String(value);
}

function Change({
  label,
  value,
}: {
  label: string;
  value: unknown;
}) {
  if (value === undefined) return null;
  const items =
    value && typeof value === "object" && !Array.isArray(value)
      ? Object.entries(value as Record<string, unknown>).map(([key, item]) => ({
          term: words(key),
          description: displayValue(item),
        }))
      : [{ term: label, description: displayValue(value) }];
  return (
    <Card padding={3}>
      <Stack gap={2}>
        <Heading level={4} size="sm">
          {label}
        </Heading>
        <DetailList label={`${label} values`} items={items} />
      </Stack>
    </Card>
  );
}

function ActivityCard({ entry }: { entry: ActivityEntry }) {
  const before = entry.details["before"];
  const after = entry.details["after"];
  return (
    <li>
      <Card as="article">
        <Stack gap={3}>
          <Cluster gap={2} justify="space-between">
            <Heading level={3} size="sm">
              {words(entry.action)}
            </Heading>
            <Text size="sm" tone="muted">
              {formatDate(entry.recordedAt)}
            </Text>
          </Cluster>
          <Text>{entry.reason}</Text>
          <DetailList
            label="Activity attribution"
            items={[
              { term: "Actor", description: <Mono>{entry.actor}</Mono> },
              {
                term: "Entity",
                description: (
                  <a href={entry.entityHref}>
                    {words(entry.entityType)} <Mono>{entry.entityId}</Mono>
                  </a>
                ),
              },
            ]}
          />
          {before !== undefined || after !== undefined ? (
            <Grid minColumnWidth="14rem" gap={3}>
              <Change label="Before" value={before} />
              <Change label="After" value={after} />
            </Grid>
          ) : null}
        </Stack>
      </Card>
    </li>
  );
}

export function ActivityTimeline({
  activity,
  emptyTitle = "No activity recorded",
  emptyDescription = "Consequential actions on this entity will appear here.",
}: {
  activity: ActivityPage;
  emptyTitle?: string;
  emptyDescription?: string;
}) {
  if (!activity.entries.length) {
    return (
      <EmptyState title={emptyTitle} description={emptyDescription} />
    );
  }
  return (
    <ol className="jx-activity-list" aria-label="Activity, most recent first">
      {activity.entries.map((entry) => (
        <ActivityCard entry={entry} key={entry.id} />
      ))}
    </ol>
  );
}

export function loadEntityActivity(
  entityType: string,
  entityId: string | number | undefined,
): Promise<ActivityPage> {
  if (entityId === undefined) {
    throw new Error("The entity identifier is required.");
  }
  return api<ActivityPage>(
    `/api/admin/activity?entityType=${encodeURIComponent(entityType)}&entityId=${encodeURIComponent(String(entityId))}`,
  );
}

function allowedSearch(search: URLSearchParams): URLSearchParams {
  const result = new URLSearchParams();
  for (const key of [
    "actor",
    "action",
    "entityType",
    "entityId",
    "dateFrom",
    "dateTo",
    "page",
  ]) {
    const value = search.get(key)?.trim();
    if (value) result.set(key, value);
  }
  return result;
}

export async function adminActivityLoader({
  request,
}: LoaderFunctionArgs): Promise<ActivityPage> {
  const search = allowedSearch(new URL(request.url).searchParams);
  return api<ActivityPage>(`/api/admin/activity?${search.toString()}`);
}

function pageHref(search: URLSearchParams, page: number): string {
  const next = allowedSearch(search);
  next.set("page", String(page));
  return `?${next.toString()}`;
}

export function AdminActivityRoute() {
  const activity = useLoaderData<ActivityPage>();
  const search = new URLSearchParams(useLocation().search);
  useDocumentTitle("Activity");

  return (
    <Page
      title="Activity"
      description="Investigate consequential changes across Jawnix. Filters and page state stay in the URL so this view can be shared."
    >
      <Stack gap={6}>
        <Section
          title="Filters"
          description="Filters are combined and applied on the server."
        >
          <Card>
            <Form method="get" className="jx-activity-filters">
              <Grid minColumnWidth="13rem" gap={3}>
                <Field label="Actor">
                  <Input name="actor" defaultValue={search.get("actor") ?? ""} />
                </Field>
                <Field label="Action">
                  <Input
                    name="action"
                    defaultValue={search.get("action") ?? ""}
                    placeholder="customer_updated"
                  />
                </Field>
                <Field label="Entity type">
                  <Select
                    name="entityType"
                    defaultValue={search.get("entityType") ?? ""}
                  >
                    {ENTITY_TYPES.map(([value, label]) => (
                      <option value={value} key={value || "all"}>
                        {label}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Entity ID">
                  <Input
                    name="entityId"
                    defaultValue={search.get("entityId") ?? ""}
                  />
                </Field>
                <Field label="From date">
                  <Input
                    type="date"
                    name="dateFrom"
                    defaultValue={search.get("dateFrom") ?? ""}
                  />
                </Field>
                <Field label="Through date">
                  <Input
                    type="date"
                    name="dateTo"
                    defaultValue={search.get("dateTo") ?? ""}
                  />
                </Field>
              </Grid>
              <Cluster gap={2}>
                <Button type="submit" variant="primary">
                  Apply filters
                </Button>
                <ActionLink href="/app/admin/activity" variant="ghost">
                  Clear filters
                </ActionLink>
              </Cluster>
            </Form>
          </Card>
        </Section>

        <Section
          title="Results"
          description="Most recent first. Activity is read-only."
        >
          <Stack gap={4}>
            <Text role="status" aria-live="polite" size="sm" tone="muted">
              {activity.total.toLocaleString()} entr
              {activity.total === 1 ? "y" : "ies"} · page {activity.page} of{" "}
              {activity.pages}
            </Text>
            <ActivityTimeline
              activity={activity}
              emptyTitle="No Activity matches these filters"
              emptyDescription="Change or clear the filters. No recorded entries were changed or removed."
            />
            {activity.pages > 1 ? (
              <nav aria-label="Activity pages">
                <Cluster gap={2} justify="space-between">
                  {activity.page > 1 ? (
                    <Link to={pageHref(search, activity.page - 1)}>
                      Previous page
                    </Link>
                  ) : (
                    <Text size="sm" tone="muted">
                      First page
                    </Text>
                  )}
                  {activity.page < activity.pages ? (
                    <Link to={pageHref(search, activity.page + 1)}>
                      Next page
                    </Link>
                  ) : (
                    <Text size="sm" tone="muted">
                      Last page
                    </Text>
                  )}
                </Cluster>
              </nav>
            ) : null}
          </Stack>
        </Section>
      </Stack>
    </Page>
  );
}
