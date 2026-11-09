import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import type { LoaderFunctionArgs } from "react-router";
import { useLoaderData, useLocation, useNavigate } from "react-router";

import { ActionLink, Button } from "../../design-system/primitives/Button";
import { Dialog } from "../../design-system/primitives/Dialog";
import { ErrorState, Loading } from "../../design-system/primitives/feedback";
import { Field, Input, Select } from "../../design-system/primitives/form";
import { Kpi, KpiRow } from "../../design-system/primitives/kpi";
import { Cluster, Page, Section } from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import type { StatusTone } from "../../design-system/primitives/status";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { Text } from "../../design-system/primitives/typography";
import { api } from "../auth/adminMFA";
import { formatCount, formatDay } from "../operator/format";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import "./AdminSourcePerformance.css";

/**
 * Source Performance: descriptive nightly outcomes by Source Segment, the
 * full table behind the Insights summary (moved from Acquisition in A5).
 *
 * This screen never re-derives Niche mappings or Source Recommendations —
 * those decisions live in Setup. It only reads the evidence the optimizer
 * reads, so an operator can see the trend behind a segment without a second
 * source of truth. A rate shows "—" until there are outcomes to divide by.
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
  ["", "Any"],
  ["eligible", "Eligible"],
  ["eligible_no_action", "Eligible · no action"],
  ["unconfirmed_niche", "Unconfirmed Niche"],
  ["insufficient_worked_leads", "Insufficient worked Leads"],
  ["insufficient_quality_ratings", "Insufficient Quality Ratings"],
  ["insufficient_peer_data", "Insufficient peer data"],
] as const;

const ACTION_OPTIONS = [
  ["", "Any"],
  ["expand", "Expand"],
  ["reduce", "Reduce"],
  ["pause", "Pause"],
  ["notes_only", "Notes only"],
  ["prescriptive_dormant", "Prescriptive dormant"],
] as const;

// Black is decided or in force; grey is not enough evidence yet. Red is
// reserved for what needs the operator, and nothing here does (V4).
const CONFIDENCE_TONES: Record<string, StatusTone> = {
  eligible: "success",
  eligible_no_action: "info",
};

const ACTION_TONES: Record<string, StatusTone> = {
  expand: "success",
  reduce: "success",
  pause: "success",
  notes_only: "info",
};

function words(value: string): string {
  const text = value.replaceAll("_", " ").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "Unknown";
}

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

/** A rate only when its denominator has something in it: a segment nobody
 *  has reported on has no rate, not a 0% rate. */
function rate(value: number | null | undefined, denominator: number | undefined): string {
  return denominator ? formatPercent(value) : "—";
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

function SummaryStats({
  global,
  legacy,
}: {
  global: SourcePerformanceGlobal;
  legacy: SourcePerformanceLegacy;
}) {
  return (
    <>
      <KpiRow label="All-time summary">
        <Kpi
          label="Delivered · all time"
          value={formatCount(global.delivered)}
          meta={
            legacy.delivered > 0
              ? `${formatCount(legacy.delivered)} legacy-source deliveries excluded`
              : "Google Maps sources"
          }
        />
        <Kpi
          label="Outcomes reported"
          value={formatCount(global.worked)}
          meta={`${formatCount(global.rated)} quality ratings`}
        />
        <Kpi
          label="Positive rate"
          value={rate(global.rates.positiveResponse, global.worked)}
          meta={`${formatCount(global.positiveResponses)} positive · ${formatCount(global.appointmentsBooked)} booked`}
        />
        <Kpi
          label="Good rate"
          value={rate(global.rates.good, global.rated)}
          meta={`${formatCount(global.good)} good · ${formatCount(global.poor)} poor`}
        />
      </KpiRow>
      {global.worked === 0 && global.rated === 0 ? (
        <Text size="sm" tone="muted">
          Rates stay empty until customers report outcomes.
        </Text>
      ) : null}
    </>
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
    <form onSubmit={submit} aria-label="Source Performance filters" className="jx-source-filters">
      <Field label="From date">
        <Input type="date" name="start_date" defaultValue={search.get("start_date") ?? ""} />
      </Field>
      <Field label="Through date">
        <Input type="date" name="end_date" defaultValue={search.get("end_date") ?? ""} />
      </Field>
      <Field label="State">
        <Input name="state" defaultValue={search.get("state") ?? ""} placeholder="TX" />
      </Field>
      <Field label="Keyword">
        <Input name="keyword" defaultValue={search.get("keyword") ?? ""} placeholder="roofing contractor" />
      </Field>
      <Field label="Niche">
        <Input name="niche" defaultValue={search.get("niche") ?? ""} placeholder="Roofing" />
      </Field>
      <Field label="Confidence">
        <Select name="confidence" defaultValue={search.get("confidence") ?? ""}>
          {CONFIDENCE_OPTIONS.map(([value, label]) => (
            <option value={value} key={value || "all"}>
              {label}
            </option>
          ))}
        </Select>
      </Field>
      <Field label="Action">
        <Select name="action_state" defaultValue={search.get("action_state") ?? ""}>
          {ACTION_OPTIONS.map(([value, label]) => (
            <option value={value} key={value || "all"}>
              {label}
            </option>
          ))}
        </Select>
      </Field>
      <label className="jx-source-filters__check">
        <input
          type="checkbox"
          checked={latest}
          onChange={(event) => setLatest(event.currentTarget.checked)}
        />
        Latest snapshot only
      </label>
      <Cluster gap={2} className="jx-source-filters__actions">
        <Button type="submit" variant="primary">
          Apply filters
        </Button>
        <ActionLink href="/app/admin/insights/sources" variant="ghost">
          Clear filters
        </ActionLink>
      </Cluster>
    </form>
  );
}

function performanceColumns(
  onSelect: (segment: string) => void,
  now: Date,
): DataColumn<SourcePerformanceRow>[] {
  return [
    {
      key: "segment",
      header: "Segment",
      cell: (row) => (
        <button
          type="button"
          className="jx-source-performance-keyword"
          onClick={() => onSelect(row.segment)}
        >
          {row.keyword}
        </button>
      ),
    },
    { key: "state", header: "State", cell: (row) => row.state },
    {
      key: "niche",
      header: "Niche",
      cell: (row) =>
        row.niche ? `${row.niche}${row.nicheConfirmed ? "" : " (unconfirmed)"}` : "—",
    },
    { key: "date", header: "Snapshot", cell: (row) => formatDay(`${row.date}T12:00:00`, now) },
    {
      key: "delivered",
      header: "Delivered",
      numeric: true,
      cell: (row) => formatCount(row.counts.delivered ?? 0),
    },
    { key: "worked", header: "Worked", numeric: true, cell: (row) => formatCount(row.counts.worked ?? 0) },
    { key: "rated", header: "Rated", numeric: true, cell: (row) => formatCount(row.counts.rated ?? 0) },
    {
      key: "positive",
      header: "Positive",
      numeric: true,
      cell: (row) => rate(row.rates.positiveResponse, row.counts.worked),
    },
    {
      key: "booked",
      header: "Booked",
      numeric: true,
      cell: (row) => rate(row.rates.appointmentBooked, row.counts.worked),
    },
    { key: "good", header: "Good", numeric: true, cell: (row) => rate(row.rates.good, row.counts.rated) },
    {
      key: "trend",
      header: "Trend",
      numeric: true,
      cell: (row) => (row.counts.worked ? trendLabel(row.trend.positiveResponse) : "—"),
    },
    {
      key: "confidence",
      header: "Confidence",
      cell: (row) => (
        <StatusBadge tone={CONFIDENCE_TONES[row.confidence] ?? "neutral"}>
          {words(row.confidence)}
        </StatusBadge>
      ),
    },
    {
      key: "action",
      header: "Action",
      cell: (row) => (
        <StatusBadge tone={ACTION_TONES[row.actionState] ?? "neutral"}>
          {words(row.actionState)}
        </StatusBadge>
      ),
    },
  ];
}

function historyColumns(now: Date): DataColumn<SourcePerformanceHistoryRow>[] {
  return [
    { key: "date", header: "Snapshot", cell: (row) => formatDay(`${row.date}T12:00:00`, now) },
    { key: "worked", header: "Worked", numeric: true, cell: (row) => formatCount(row.counts.worked ?? 0) },
    { key: "rated", header: "Rated", numeric: true, cell: (row) => formatCount(row.counts.rated ?? 0) },
    { key: "good", header: "Good", numeric: true, cell: (row) => rate(row.rates.good, row.counts.rated) },
    {
      key: "positive",
      header: "Positive",
      numeric: true,
      cell: (row) => rate(row.rates.positiveResponse, row.counts.worked),
    },
    {
      key: "confidence",
      header: "Confidence",
      cell: (row) => (
        <StatusBadge tone={CONFIDENCE_TONES[row.confidence] ?? "neutral"}>
          {words(row.confidence)}
        </StatusBadge>
      ),
    },
    { key: "note", header: "Note", cell: (row) => row.suggestionNote || "—" },
  ];
}

/** "roofing contractor · TX" for the stored "TX::roofing contractor". */
function segmentName(segment: string): string {
  const [state, keyword] = segment.split("::");
  return keyword ? `${keyword} · ${state}` : segment;
}

/** Loads a Source Segment's full snapshot history in a Dialog rather than an
 *  alert, so the evidence behind a keyword's current row stays inspectable
 *  and dismissible. */
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
      title={segment ? `History · ${segmentName(segment)}` : "History"}
      description="Every nightly snapshot recorded for this Source Segment, most recent first."
    >
      {busy ? (
        <Loading label="Loading history…" />
      ) : failure ? (
        <ErrorState description={failure} />
      ) : rows ? (
        <DataTable
          label="Snapshot history"
          columns={historyColumns(new Date())}
          rows={rows}
          rowKey={(row) => row.id}
          empty="No nightly snapshot has been recorded for this Source Segment yet."
        />
      ) : null}
    </Dialog>
  );
}

export function AdminSourcePerformanceRoute() {
  const data = useLoaderData<SourcePerformanceData>();
  const search = new URLSearchParams(useLocation().search);
  const [historySegment, setHistorySegment] = useState<string | null>(null);
  useDocumentTitle("Source performance");

  const csvHref = `/api/admin/source-performance.csv?${filteredQuery(search).toString()}`;
  const now = new Date();

  return (
    <Page
      title="Source performance"
      width="data"
      density="data"
      description="Outcomes by Source Segment from each nightly snapshot. Select a keyword for its history."
      actions={<ActionLink href="/app/admin/insights">Back to Insights</ActionLink>}
    >
      <SummaryStats global={data.global} legacy={data.legacy} />

      <FiltersForm search={search} />

      <Section title="Nightly rows">
        <DataTable
          label="Nightly Source Performance rows"
          columns={performanceColumns(setHistorySegment, now)}
          rows={data.rows}
          rowKey={(row) => row.id}
          empty="No nightly rows match these filters. Widen the date range or clear a filter."
        />
        <Cluster justify="space-between">
          <Text size="sm" tone="muted" role="status">
            {formatCount(data.rows.length)} row{data.rows.length === 1 ? "" : "s"} shown
          </Text>
          <ActionLink href={csvHref} variant="ghost">
            Export CSV
          </ActionLink>
        </Cluster>
      </Section>

      <HistoryDialog
        segment={historySegment}
        onClose={() => setHistorySegment(null)}
      />
    </Page>
  );
}
