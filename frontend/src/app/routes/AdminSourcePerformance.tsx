import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import type { LoaderFunctionArgs } from "react-router";
import { useLoaderData, useLocation, useNavigate } from "react-router";

import { ActionLink, Button } from "../../design-system/primitives/Button";
import { Dialog } from "../../design-system/primitives/Dialog";
import {
  EmptyState,
  ErrorState,
  Loading,
} from "../../design-system/primitives/feedback";
import { Field, Input, Select } from "../../design-system/primitives/form";
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
import { TerminalWorkspace } from "../../design-system/primitives/terminal";
import type { TerminalDestination } from "../../design-system/primitives/terminal";
import {
  LabelText,
  Mono,
  Text,
  VisuallyHidden,
} from "../../design-system/primitives/typography";
import { api } from "../auth/adminMFA";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import "./AdminSourcePerformance.css";

/**
 * Source Performance (#173 A1): descriptive nightly outcomes by Source
 * Segment, restored as its own destination under Acquisition.
 *
 * This screen never re-derives Niche mappings or Source Recommendations —
 * those decisions live on Acquisition itself. It only reads the same
 * evidence Acquisition's optimizer reads and renders it, so an operator can
 * see the trend behind a recommendation without a second source of truth.
 */

export interface SourcePerformanceRow {
  id: string;
  date: string;
  segment: string;
  state: string;
  keyword: string;
  niche: string;
  nicheConfirmed: boolean;
  counts: Record<string, number>;
  rates: Record<string, number | null>;
  intervals: Record<string, unknown>;
  trend: Record<string, number | null>;
  confidence: string;
  actionState: string;
  evidenceChecksum: string;
}

export interface SourcePerformanceHistoryRow extends SourcePerformanceRow {
  suggestionNote: string | null;
}

export interface SourcePerformanceGlobal {
  delivered: number;
  worked: number;
  rated: number;
  good: number;
  poor: number;
  positiveResponses: number;
  appointmentsBooked: number;
  rates: {
    good: number;
    positiveResponse: number;
    appointmentBooked: number;
  };
  prescriptive: boolean;
}

export interface SourcePerformanceLegacy {
  delivered: number;
  excludedFromRecommendations: boolean;
}

export interface SourcePerformanceData {
  // Cohorts/segments back the dormant Worked-Leads prescription and the
  // keyword outcome analytics elsewhere; this screen renders neither.
  cohorts: unknown[];
  segments: unknown[];
  global: SourcePerformanceGlobal;
  legacy: SourcePerformanceLegacy;
  rows: SourcePerformanceRow[];
}

const DESTINATIONS: TerminalDestination[] = [
  { label: "Acquisition review", href: "/app/admin/acquisition" },
  {
    label: "Source Performance",
    href: "/app/admin/acquisition/performance",
    current: true,
  },
  { label: "Scraper Operations", href: "/app/admin/acquisition/scraper" },
  { label: "Administrator security", href: "/app/admin/security" },
  { label: "Exit to Overview", href: "/app/admin/overview" },
];

const TEXT_FILTER_KEYS = [
  "start_date",
  "end_date",
  "state",
  "keyword",
  "niche",
  "confidence",
  "action_state",
] as const;

const CONFIDENCE_OPTIONS = [
  ["", "Every confidence"],
  ["eligible", "Eligible"],
  ["eligible_no_action", "Eligible · no action"],
  ["unconfirmed_niche", "Unconfirmed Niche"],
  ["insufficient_worked_leads", "Insufficient worked Leads"],
  ["insufficient_quality_ratings", "Insufficient Quality Ratings"],
  ["insufficient_peer_data", "Insufficient peer data"],
] as const;

const ACTION_OPTIONS = [
  ["", "Every action"],
  ["expand", "Expand"],
  ["reduce", "Reduce"],
  ["pause", "Pause"],
  ["notes_only", "Notes only"],
  ["prescriptive_dormant", "Prescriptive dormant"],
] as const;

const CONFIDENCE_TONES: Record<string, StatusTone> = {
  eligible: "success",
  eligible_no_action: "info",
  unconfirmed_niche: "neutral",
  insufficient_worked_leads: "warning",
  insufficient_quality_ratings: "warning",
  insufficient_peer_data: "warning",
};

const ACTION_TONES: Record<string, StatusTone> = {
  expand: "success",
  reduce: "warning",
  pause: "danger",
  notes_only: "info",
  prescriptive_dormant: "neutral",
};

/** Absence and `"true"` both mean the backend's own default: only the latest
 *  snapshot per Source Segment. Only an explicit `"false"` requests every
 *  snapshot in range, which is what the checkbox needs to be able to say. */
function isLatest(search: URLSearchParams): boolean {
  return search.get("latest") !== "false";
}

/** The one query string every reader of this screen shares — the loader, the
 *  CSV export link, and the filters form all build the request from the same
 *  allow-list so none of them can drift from what the backend accepts. */
function filteredQuery(search: URLSearchParams): URLSearchParams {
  const result = new URLSearchParams();
  for (const key of TEXT_FILTER_KEYS) {
    const value = search.get(key)?.trim();
    if (value) result.set(key, value);
  }
  result.set("latest", isLatest(search) ? "true" : "false");
  return result;
}

export async function sourcePerformanceLoader({
  request,
}: LoaderFunctionArgs): Promise<SourcePerformanceData> {
  const search = filteredQuery(new URL(request.url).searchParams);
  return api<SourcePerformanceData>(
    `/api/admin/source-performance?${search.toString()}`,
  );
}

function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function trendLabel(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const points = value * 100;
  if (Math.abs(points) < 0.05) return "flat";
  return `${points > 0 ? "▲" : "▼"} ${Math.abs(points).toFixed(1)} pts`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "The history could not be loaded.";
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <Card>
      <Stack gap={1}>
        <LabelText>{label}</LabelText>
        <Text size="lg" weight="bold">
          {typeof value === "number" ? value.toLocaleString() : value}
        </Text>
      </Stack>
    </Card>
  );
}

function SummaryStats({
  global,
  legacy,
}: {
  global: SourcePerformanceGlobal;
  legacy: SourcePerformanceLegacy;
}) {
  return (
    <Stack gap={3}>
      <Grid minColumnWidth="11rem">
        <Stat label="Delivered" value={global.delivered} />
        <Stat label="Worked" value={global.worked} />
        <Stat label="Rated" value={global.rated} />
        <Stat label="Positive responses" value={global.positiveResponses} />
        <Stat label="Appointments booked" value={global.appointmentsBooked} />
        <Stat label="Good rate" value={formatPercent(global.rates.good)} />
        <Stat
          label="Positive rate"
          value={formatPercent(global.rates.positiveResponse)}
        />
        <Stat
          label="Booked rate"
          value={formatPercent(global.rates.appointmentBooked)}
        />
      </Grid>
      {legacy.delivered > 0 ? (
        <Text size="sm" tone="muted">
          {legacy.delivered.toLocaleString()} legacy-source deliveries are
          excluded from these figures and from Source Recommendations.
        </Text>
      ) : null}
    </Stack>
  );
}

function FiltersForm({ search }: { search: URLSearchParams }) {
  const navigate = useNavigate();
  const [latest, setLatest] = useState(isLatest(search));

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const next = new URLSearchParams();
    for (const key of TEXT_FILTER_KEYS) {
      const value = String(data.get(key) ?? "").trim();
      if (value) next.set(key, value);
    }
    next.set("latest", latest ? "true" : "false");
    void navigate(`?${next.toString()}`);
  }

  return (
    <Card>
      <form onSubmit={submit} aria-label="Source Performance filters">
        <Stack gap={4}>
          <Grid minColumnWidth="12rem" gap={3}>
            <Field label="From date">
              <Input
                type="date"
                name="start_date"
                defaultValue={search.get("start_date") ?? ""}
              />
            </Field>
            <Field label="Through date">
              <Input
                type="date"
                name="end_date"
                defaultValue={search.get("end_date") ?? ""}
              />
            </Field>
            <Field label="State">
              <Input
                name="state"
                defaultValue={search.get("state") ?? ""}
                placeholder="TX"
              />
            </Field>
            <Field label="Keyword">
              <Input
                name="keyword"
                defaultValue={search.get("keyword") ?? ""}
                placeholder="roofing contractor"
              />
            </Field>
            <Field label="Niche">
              <Input
                name="niche"
                defaultValue={search.get("niche") ?? ""}
                placeholder="Roofing"
              />
            </Field>
            <Field label="Confidence">
              <Select
                name="confidence"
                defaultValue={search.get("confidence") ?? ""}
              >
                {CONFIDENCE_OPTIONS.map(([value, label]) => (
                  <option value={value} key={value || "all"}>
                    {label}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Action">
              <Select
                name="action_state"
                defaultValue={search.get("action_state") ?? ""}
              >
                {ACTION_OPTIONS.map(([value, label]) => (
                  <option value={value} key={value || "all"}>
                    {label}
                  </option>
                ))}
              </Select>
            </Field>
          </Grid>
          <label className="jx-source-performance-toggle">
            <input
              type="checkbox"
              checked={latest}
              onChange={(event) => setLatest(event.currentTarget.checked)}
            />
            Only the latest snapshot per Source Segment
          </label>
          <Cluster gap={2}>
            <Button type="submit" variant="primary">
              Apply filters
            </Button>
            <ActionLink
              href="/app/admin/acquisition/performance"
              variant="ghost"
            >
              Clear filters
            </ActionLink>
          </Cluster>
        </Stack>
      </form>
    </Card>
  );
}

function PerformanceRow({
  row,
  onSelect,
}: {
  row: SourcePerformanceRow;
  onSelect: (segment: string) => void;
}) {
  return (
    <tr>
      <td data-label="Date">
        <Mono>{row.date}</Mono>
      </td>
      <td data-label="State">{row.state}</td>
      <th scope="row" data-label="Keyword">
        <button
          type="button"
          className="jx-source-performance-keyword"
          onClick={() => onSelect(row.segment)}
        >
          {row.keyword}
        </button>
      </th>
      <td data-label="Niche">
        {row.niche || "—"}
        {row.niche && !row.nicheConfirmed ? " (unconfirmed)" : ""}
      </td>
      <td data-label="Worked">{(row.counts.worked ?? 0).toLocaleString()}</td>
      <td data-label="Rated">{(row.counts.rated ?? 0).toLocaleString()}</td>
      <td data-label="Good rate">{formatPercent(row.rates.good)}</td>
      <td data-label="Positive rate">
        {formatPercent(row.rates.positiveResponse)}
      </td>
      <td data-label="Booked rate">
        {formatPercent(row.rates.appointmentBooked)}
      </td>
      <td data-label="Trend">{trendLabel(row.trend.positiveResponse)}</td>
      <td data-label="Confidence">
        <StatusBadge tone={CONFIDENCE_TONES[row.confidence] ?? "neutral"}>
          {row.confidence.replaceAll("_", " ")}
        </StatusBadge>
      </td>
      <td data-label="Action">
        <StatusBadge tone={ACTION_TONES[row.actionState] ?? "neutral"}>
          {row.actionState.replaceAll("_", " ")}
        </StatusBadge>
      </td>
    </tr>
  );
}

function PerformanceTable({
  rows,
  onSelect,
}: {
  rows: SourcePerformanceRow[];
  onSelect: (segment: string) => void;
}) {
  if (!rows.length) {
    return (
      <EmptyState
        title="No nightly rows match these filters"
        description="Widen the date range or clear a filter to see recorded Source Performance snapshots."
      />
    );
  }
  return (
    <div className="jx-source-performance-table-wrap">
      <table className="jx-source-performance-table">
        <caption>
          <VisuallyHidden>
            Nightly Source Performance rows matching the current filters
          </VisuallyHidden>
        </caption>
        <thead>
          <tr>
            <th scope="col">Date</th>
            <th scope="col">State</th>
            <th scope="col">Keyword</th>
            <th scope="col">Niche</th>
            <th scope="col">Worked</th>
            <th scope="col">Rated</th>
            <th scope="col">Good rate</th>
            <th scope="col">Positive rate</th>
            <th scope="col">Booked rate</th>
            <th scope="col">Trend</th>
            <th scope="col">Confidence</th>
            <th scope="col">Action</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <PerformanceRow row={row} onSelect={onSelect} key={row.id} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HistoryRow({ row }: { row: SourcePerformanceHistoryRow }) {
  return (
    <tr>
      <td data-label="Date">
        <Mono>{row.date}</Mono>
      </td>
      <td data-label="Worked">{(row.counts.worked ?? 0).toLocaleString()}</td>
      <td data-label="Rated">{(row.counts.rated ?? 0).toLocaleString()}</td>
      <td data-label="Good rate">{formatPercent(row.rates.good)}</td>
      <td data-label="Positive rate">
        {formatPercent(row.rates.positiveResponse)}
      </td>
      <td data-label="Confidence">
        <StatusBadge tone={CONFIDENCE_TONES[row.confidence] ?? "neutral"}>
          {row.confidence.replaceAll("_", " ")}
        </StatusBadge>
      </td>
      <td data-label="Note">{row.suggestionNote || "—"}</td>
    </tr>
  );
}

/** Loads a Source Segment's full snapshot history in a Dialog rather than an
 *  alert, so the evidence behind a keyword's current row stays inspectable
 *  and dismissible like every other consequential surface in Acquisition. */
function HistoryDialog({
  segment,
  onClose,
}: {
  segment: string | null;
  onClose: () => void;
}) {
  const [rows, setRows] = useState<SourcePerformanceHistoryRow[] | null>(
    null,
  );
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!segment) {
      setRows(null);
      setFailure("");
      return;
    }
    let active = true;
    setBusy(true);
    setFailure("");
    api<{ rows: SourcePerformanceHistoryRow[] }>(
      `/api/admin/source-performance/${encodeURIComponent(segment)}/history`,
    )
      .then((response) => {
        if (active) setRows(response.rows);
      })
      .catch((caught: unknown) => {
        if (active) setFailure(errorMessage(caught));
      })
      .finally(() => {
        if (active) setBusy(false);
      });
    return () => {
      active = false;
    };
  }, [segment]);

  return (
    <Dialog
      open={segment !== null}
      onClose={onClose}
      title={segment ? `History · ${segment}` : "History"}
      description="Every nightly snapshot recorded for this Source Segment, most recent first."
    >
      {busy ? (
        <Loading label="Loading history…" />
      ) : failure ? (
        <ErrorState description={failure} />
      ) : rows && rows.length ? (
        <div className="jx-source-performance-table-wrap">
          <table className="jx-source-performance-table">
            <caption>
              <VisuallyHidden>Snapshot history</VisuallyHidden>
            </caption>
            <thead>
              <tr>
                <th scope="col">Date</th>
                <th scope="col">Worked</th>
                <th scope="col">Rated</th>
                <th scope="col">Good rate</th>
                <th scope="col">Positive rate</th>
                <th scope="col">Confidence</th>
                <th scope="col">Note</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <HistoryRow row={row} key={row.id} />
              ))}
            </tbody>
          </table>
        </div>
      ) : rows ? (
        <EmptyState
          title="No history recorded"
          description="No nightly snapshot has been recorded for this Source Segment yet."
        />
      ) : null}
    </Dialog>
  );
}

export function AdminSourcePerformanceRoute() {
  const data = useLoaderData<SourcePerformanceData>();
  const search = new URLSearchParams(useLocation().search);
  const [historySegment, setHistorySegment] = useState<string | null>(null);
  useDocumentTitle("Source Performance");

  const csvHref = `/api/admin/source-performance.csv?${filteredQuery(search).toString()}`;

  return (
    <Page
      title="Source Performance"
      description="Descriptive nightly outcomes by Source Segment. Niche mappings and Source Recommendations stay decided on Acquisition."
    >
      <TerminalWorkspace
        status="ONLINE / DESCRIPTIVE"
        tone="online"
        label="Source Performance workspace"
        destinations={DESTINATIONS}
      >
        <Stack gap={6}>
          <Section
            title="All-time summary"
            description="Totals across every delivered google_maps Lead, independent of the filters below."
          >
            <SummaryStats global={data.global} legacy={data.legacy} />
          </Section>

          <Section
            title="Filters"
            description="Combined and applied on the server. Filters stay in the URL so this view can be shared."
          >
            <FiltersForm search={search} />
          </Section>

          <Section
            title="Nightly rows"
            description="One row per recorded nightly snapshot. Click a keyword to see its full history."
          >
            <Stack gap={3}>
              <Cluster justify="space-between">
                <Text size="sm" tone="muted" role="status">
                  {data.rows.length.toLocaleString()} row
                  {data.rows.length === 1 ? "" : "s"} shown
                </Text>
                <ActionLink href={csvHref} variant="ghost">
                  Export CSV
                </ActionLink>
              </Cluster>
              <PerformanceTable
                rows={data.rows}
                onSelect={setHistorySegment}
              />
            </Stack>
          </Section>
        </Stack>
      </TerminalWorkspace>

      <HistoryDialog
        segment={historySegment}
        onClose={() => setHistorySegment(null)}
      />
    </Page>
  );
}
