import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { LoaderFunctionArgs } from "react-router";
import { Link, useLoaderData, useNavigation, useSearchParams } from "react-router";

import { ButtonLink } from "../../design-system/primitives/Button";
import { DetailList } from "../../design-system/primitives/detail";
import { EmptyState } from "../../design-system/primitives/feedback";
import { Field, Input, Select } from "../../design-system/primitives/form";
import { Card, Cluster, Grid, Page, Stack } from "../../design-system/primitives/layout";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { Heading, Mono, Text } from "../../design-system/primitives/typography";
import { api } from "../api";
import { formatCount, formatDateTime } from "../operator/format";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import "./AdminActivity.css";

/** One Audit Entry, as `query_activity` in jawnix/activity.py returns it.
 *  The display fields are optional so older callers' fixtures still fit;
 *  every reader falls back to the stored identifiers. */
export interface ActivityEntry {
  id: string;
  action: string;
  /** "Batch approved", "Refused: permanent deletion". */
  actionLabel?: string;
  refused?: boolean;
  entityType: string;
  entityTypeLabel?: string;
  entityId: string;
  /** "Batch #1042", "Northstar Insurance". */
  entityLabel?: string;
  entityHref: string;
  actor: string;
  /** "Operator", "System", or a Customer user's name. */
  actorName?: string;
  reason: string;
  details: Record<string, unknown>;
  recordedAt: string;
}

export interface ActivityFilterOption {
  value: string;
  label: string;
}

export interface ActivityPage {
  entries: ActivityEntry[];
  page: number;
  pageSize: number;
  total: number;
  pages: number;
  /** The global Activity read only: every recorded record type, and
   *  everyone who has acted. */
  filters?: { entityTypes: ActivityFilterOption[]; who: ActivityFilterOption[] };
}

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

export function actionLabel(entry: ActivityEntry): string {
  return entry.actionLabel ?? words(entry.action);
}

export function recordLabel(entry: ActivityEntry): string {
  return entry.entityLabel ?? `${entry.entityTypeLabel ?? words(entry.entityType)} ${entry.entityId}`;
}

function linkPath(href: string): string {
  return href.startsWith("/app/") ? href.slice("/app".length) : href;
}

/** One value on its own line: scalars as written, structures as JSON. */
function line(key: string, value: unknown): string {
  const shown =
    value === null || value === undefined
      ? "none"
      : typeof value === "object"
        ? JSON.stringify(value)
        : String(value);
  return key ? `${key}: ${shown}` : shown;
}

function lines(value: unknown): string {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const entries = Object.entries(value as Record<string, unknown>);
    return entries.length ? entries.map(([key, item]) => line(key, item)).join("\n") : "none";
  }
  return line("", value);
}

/** What an entry changed: its safe before and after side by side, or the
 *  details it recorded when it has no before/after. */
export function ActivityChange({ entry }: { entry: ActivityEntry }) {
  const before = entry.details["before"];
  const after = entry.details["after"];
  if (before !== undefined || after !== undefined) {
    return (
      <div className="jx-activity-change">
        <div>
          <Text as="span" size="xs" tone="muted">
            Before
          </Text>
          <pre>{lines(before)}</pre>
        </div>
        <div>
          <Text as="span" size="xs" tone="muted">
            After
          </Text>
          <pre>{lines(after)}</pre>
        </div>
      </div>
    );
  }
  const rest = Object.keys(entry.details).length;
  return rest ? (
    <div className="jx-activity-change jx-activity-change--single">
      <div>
        <Text as="span" size="xs" tone="muted">
          Details
        </Text>
        <pre>{lines(entry.details)}</pre>
      </div>
    </div>
  ) : (
    <Text size="sm" tone="muted">
      No change details were recorded for this entry.
    </Text>
  );
}

export interface ActivityTableProps {
  activity: ActivityPage;
  label?: string;
  /** Leave out the Record column on a record's own page. */
  showRecord?: boolean;
  empty?: ReactNode;
  /** Where the rest is, when this shows only the newest page of a record's
   *  Activity. */
  moreHref?: string;
}

/** The global Activity page narrowed to one record. */
export function recordActivityHref(entityType: string, entityId: string | number): string {
  const query = new URLSearchParams({ entityType, entityId: String(entityId) });
  return `/app/admin/activity?${query.toString()}`;
}

/**
 * Activity as one-line rows (P8): when, who, what, which record, and why.
 * Each row opens beneath itself to show what changed.
 */
export function ActivityTable({
  activity,
  label = "Activity",
  showRecord = true,
  empty = "No activity recorded.",
  moreHref,
}: ActivityTableProps) {
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const now = new Date();

  function toggle(id: string) {
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const columns: DataColumn<ActivityEntry>[] = [
    {
      key: "when",
      header: "When",
      cell: (entry) => (
        <button
          type="button"
          className="jx-activity-toggle"
          aria-expanded={open.has(entry.id)}
          aria-controls={`activity-entry-${entry.id}`}
          aria-label={`${formatDateTime(entry.recordedAt, now)}, ${actionLabel(entry)}: ${
            open.has(entry.id) ? "hide" : "show"
          } what changed`}
          onClick={() => toggle(entry.id)}
        >
          <span aria-hidden="true" className="jx-activity-toggle__mark">
            {open.has(entry.id) ? "−" : "+"}
          </span>
          {formatDateTime(entry.recordedAt, now)}
        </button>
      ),
    },
    { key: "who", header: "Who", cell: (entry) => entry.actorName ?? entry.actor },
    {
      key: "action",
      header: "Action",
      cell: (entry) =>
        entry.refused ? (
          <Text as="span" size="sm" tone="danger">
            {actionLabel(entry)}
          </Text>
        ) : (
          actionLabel(entry)
        ),
    },
    ...(showRecord
      ? [
          {
            key: "record",
            header: "Record",
            cell: (entry: ActivityEntry) =>
              entry.entityHref.startsWith("/app/") ? (
                <Link to={linkPath(entry.entityHref)}>{recordLabel(entry)}</Link>
              ) : (
                <a href={entry.entityHref}>{recordLabel(entry)}</a>
              ),
          },
        ]
      : []),
    { key: "reason", header: "Reason", cell: (entry) => entry.reason },
  ];

  return (
    <>
      <DataTable
        label={label}
        className="jx-activity-table"
        columns={columns}
        rows={activity.entries}
        rowKey={(entry) => entry.id}
        onRowClick={(entry) => toggle(entry.id)}
        expanded={(entry) => (open.has(entry.id) ? <ActivityChange entry={entry} /> : null)}
        expandedId={(entry) => `activity-entry-${entry.id}`}
        empty={empty}
      />
      {moreHref && activity.total > activity.entries.length ? (
        <div>
          <Link to={linkPath(moreHref)}>All {formatCount(activity.total)} entries</Link>
        </div>
      ) : null}
    </>
  );
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
        <details className="jx-activity-entry">
          <summary>
            <span className="jx-activity-entry__summary">
              <span className="jx-activity-entry__action">
                {words(entry.action)}
              </span>
              <span className="jx-activity-entry__reason">{entry.reason}</span>
            </span>
            <span className="jx-activity-entry__date">
              {formatDate(entry.recordedAt)}
            </span>
          </summary>
          <Stack gap={3} className="jx-activity-entry__details">
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
        </details>
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

const SEARCH_KEYS = [
  "q",
  "who",
  "actor",
  "action",
  "entityType",
  "entityId",
  "dateFrom",
  "dateTo",
  "refused",
  "page",
] as const;

function allowedSearch(search: URLSearchParams): URLSearchParams {
  const result = new URLSearchParams();
  for (const key of SEARCH_KEYS) {
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
  if (page <= 1) next.delete("page");
  else next.set("page", String(page));
  const query = next.toString();
  return `/app/admin/activity${query ? `?${query}` : ""}`;
}

function SearchFilter({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const [draft, setDraft] = useState(value);
  const changed = useRef(onChange);
  changed.current = onChange;
  useEffect(() => setDraft(value), [value]);
  useEffect(() => {
    if (draft === value) return;
    const timer = window.setTimeout(() => changed.current(draft), 300);
    return () => window.clearTimeout(timer);
  }, [draft, value]);
  return (
    <Field label="Search">
      <Input
        type="search"
        placeholder="Action, record, or reason"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
      />
    </Field>
  );
}

export function AdminActivityRoute() {
  const activity = useLoaderData<ActivityPage>();
  const [params, setParams] = useSearchParams();
  useDocumentTitle("Activity");

  // Controls follow the address being loaded, so a filter reads as changed
  // the moment it is picked rather than when its results arrive. The pending
  // location still carries the router basename, hence the suffix test.
  const navigation = useNavigation();
  const pending = navigation.location?.pathname.endsWith("/admin/activity")
    ? new URLSearchParams(navigation.location.search)
    : null;
  const shown = pending ?? params;
  const refused = shown.get("refused") === "true";
  const [refusedChecked, setRefusedChecked] = useState(refused);
  useEffect(() => setRefusedChecked(refused), [refused]);

  function setFilter(key: string, value: string | null) {
    // Build on the address being loaded (`shown`), not the last committed one:
    // a change made while the previous navigation is still fetching must keep
    // the previous change's params.
    const next = new URLSearchParams(shown);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== "page") next.delete("page");
    setParams(next, { replace: true, preventScrollReset: true });
  }

  const entityTypes = activity.filters?.entityTypes ?? [];
  const who = activity.filters?.who ?? [];
  const entityType = shown.get("entityType") ?? "";
  const whoValue = shown.get("who") ?? "";
  const narrowed = params.get("entityId") || params.get("actor") || params.get("action");
  const first = activity.total ? (activity.page - 1) * activity.pageSize + 1 : 0;
  const last = Math.min(activity.total, activity.page * activity.pageSize);

  return (
    <Page
      title="Activity"
      width="data"
      density="data"
      description="Every consequential change, newest first."
    >
      <div className="jx-activity-filters">
        <SearchFilter value={params.get("q") ?? ""} onChange={(value) => setFilter("q", value.trim() || null)} />
        <Field label="Who">
          <Select value={whoValue} onChange={(event) => setFilter("who", event.target.value || null)}>
            <option value="">Anyone</option>
            {who.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Record type">
          <Select value={entityType} onChange={(event) => setFilter("entityType", event.target.value || null)}>
            <option value="">All types</option>
            {entityTypes.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
            {entityType && !entityTypes.some((option) => option.value === entityType) ? (
              <option value={entityType}>{words(entityType)}</option>
            ) : null}
          </Select>
        </Field>
        <Field label="From">
          <Input
            type="date"
            value={shown.get("dateFrom") ?? ""}
            onChange={(event) => setFilter("dateFrom", event.target.value || null)}
          />
        </Field>
        <Field label="Through">
          <Input
            type="date"
            value={shown.get("dateTo") ?? ""}
            onChange={(event) => setFilter("dateTo", event.target.value || null)}
          />
        </Field>
        <label className="jx-activity-filters__check">
          <input
            type="checkbox"
            checked={refusedChecked}
            onChange={(event) => {
              setRefusedChecked(event.target.checked);
              setFilter("refused", event.target.checked ? "true" : null);
            }}
          />
          Refused only
        </label>
      </div>
      {narrowed ? (
        <Cluster gap={3}>
          <Text size="sm" tone="muted">
            Narrowed to{" "}
            {[
              params.get("entityId") ? `one ${words(params.get("entityType") ?? "record").toLowerCase()}` : null,
              params.get("action") ? words(params.get("action") ?? "") : null,
              params.get("actor") ? "one actor" : null,
            ]
              .filter(Boolean)
              .join(" · ")}
            .
          </Text>
          <Link to="/admin/activity">Show all activity</Link>
        </Cluster>
      ) : null}

      <ActivityTable
        activity={activity}
        empty="No activity matches these filters. Nothing recorded was changed or removed."
      />

      <Cluster gap={3} justify="space-between">
        <Text size="sm" tone="muted" role="status">
          {activity.total
            ? `Showing ${formatCount(first)}–${formatCount(last)} of ${formatCount(activity.total)}`
            : "No entries"}
        </Text>
        {activity.pages > 1 ? (
          <nav aria-label="Activity pages" className="jx-activity-pages">
            {activity.page > 1 ? (
              <ButtonLink variant="secondary" href={pageHref(params, activity.page - 1)}>
                Previous
              </ButtonLink>
            ) : null}
            <Text as="span" size="sm">
              Page {activity.page} of {activity.pages}
            </Text>
            {activity.page < activity.pages ? (
              <ButtonLink variant="secondary" href={pageHref(params, activity.page + 1)}>
                Next
              </ButtonLink>
            ) : null}
          </nav>
        ) : null}
      </Cluster>
    </Page>
  );
}
