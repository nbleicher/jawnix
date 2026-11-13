import type { CSSProperties } from "react";
import { Link, useLoaderData } from "react-router";

import { ActionLink } from "../../design-system/primitives/Button";
import { Kpi, KpiRow } from "../../design-system/primitives/kpi";
import { Page, Section } from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import type { StatusTone } from "../../design-system/primitives/status";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { Text } from "../../design-system/primitives/typography";
import { api } from "../api";
import { routePath } from "../routePath";
import { formatCents } from "../billing/money";
import { formatCount, formatDay, plural } from "../operator/format";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import "./AdminInsights.css";

/**
 * Insights (P6): the weekly review. Money, deliveries, and reports against
 * the prior 30 days; revenue by month; how each Customer is trending; which
 * Source Segments produce good leads; and the Nightly Review history.
 *
 * Mirrors `operator_insights` in jawnix/insights.py.
 */

export interface InsightsCustomerTrend {
  customer: { id: number; name: string; href: string };
  delivered: number;
  priorDelivered: number;
  reports: number;
  shortBy: number | null;
}

export interface InsightsMonth {
  /** "2026-11", UTC calendar month. */
  month: string;
  creditsUsedCents: number;
  leadsDelivered: number;
  batches: number;
  /** Days so far, for the current month only. */
  daysSoFar: number | null;
}

export interface InsightsSourceRow {
  segment: string;
  keyword: string;
  state: string;
  niche: string;
  nicheConfirmed: boolean;
  delivered: number;
  worked: number;
  rated: number;
  positive: number;
  booked: number;
  poor: number;
  /** Null when there is nothing to divide by: no outcomes is not 0%. */
  positiveRate: number | null;
  bookedRate: number | null;
  poorRate: number | null;
}

export interface NightlyReviewRow {
  id: string;
  scraperRunId: number | null;
  reviewDate: string | null;
  status: string;
  summary: Record<string, unknown>;
  telegramDeliveryState: string;
  telegramMessageId: string;
  telegramDeliveryError: string;
  createdAt: string;
  reconcilable: boolean;
}

export interface InsightsData {
  generatedAt: string;
  windowDays: number;
  creditsUsed: { cents: number; priorCents: number };
  creditsBought: {
    cents: number;
    priorCents: number;
    purchases: number;
    priorPurchases: number;
  };
  delivered: {
    leads: number;
    priorLeads: number;
    batches: number;
    priorBatches: number;
  };
  leadReports: { count: number; priorCount: number };
  revenueByMonth: InsightsMonth[];
  customerTrends: InsightsCustomerTrend[];
  sourcePerformance: {
    snapshotDate: string | null;
    windowDays: number | null;
    segments: number;
    outcomesReported: number;
    rows: InsightsSourceRow[];
  };
  nightlyReviews: NightlyReviewRow[];
}

export async function adminInsightsLoader(): Promise<InsightsData> {
  return api<InsightsData>("/api/admin/insights");
}

/** Signed whole percent with a true minus sign ("+50%", "−15%"). */
function percentChange(current: number, prior: number): number | null {
  if (prior <= 0) return null;
  return Math.round(((current - prior) / prior) * 100);
}

function signed(change: number): string {
  if (change === 0) return "0%";
  return `${change > 0 ? "+" : "−"}${Math.abs(change)}%`;
}

/** "Up 18% on the prior 30 days" for a tile's supporting line. */
export function changeLine(
  current: number,
  prior: number,
  windowDays: number,
  none: string,
): string {
  const window = `the prior ${windowDays} days`;
  const change = percentChange(current, prior);
  if (change === null) {
    return current > 0 ? `${none} in ${window}` : `${none} in ${window} either`;
  }
  if (change === 0) return `Level with ${window}`;
  return `${change > 0 ? "Up" : "Down"} ${Math.abs(change)}% on ${window}`;
}

function monthLabel(month: InsightsMonth, now: Date): string {
  const [year, index] = month.month.split("-").map(Number);
  const date = new Date(year ?? now.getFullYear(), (index ?? 1) - 1, 1);
  const name = date.toLocaleString("en-US", { month: "short" });
  const label = date.getFullYear() === now.getFullYear() ? name : `${name} ${date.getFullYear()}`;
  return month.daysSoFar ? `${label} (${plural(month.daysSoFar, "day")})` : label;
}

function formatRate(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

export function segmentLabel(row: { keyword: string; state: string }): string {
  return `${row.keyword} · ${row.state}`;
}

// ------------------------------------------------------------ Revenue by month

function RevenueBar({ cents, max }: { cents: number; max: number }) {
  // A single series beside its own figures: decorative, so hidden from
  // assistive technology; the numbers in the row carry the value.
  const share = max > 0 ? cents / max : 0;
  return (
    <span
      className="jx-insights-bar"
      aria-hidden="true"
      style={{ "--jx-insights-share": share } as CSSProperties}
    />
  );
}

function revenueColumns(months: InsightsMonth[], now: Date): DataColumn<InsightsMonth>[] {
  const max = Math.max(0, ...months.map((month) => month.creditsUsedCents));
  return [
    { key: "month", header: "Month", cell: (month) => monthLabel(month, now) },
    {
      key: "used",
      header: "Credits used",
      numeric: true,
      cell: (month) => formatCents(month.creditsUsedCents),
    },
    {
      key: "delivered",
      header: "Leads delivered",
      numeric: true,
      cell: (month) => formatCount(month.leadsDelivered),
    },
    {
      key: "bar",
      header: "Share of best month",
      hideWhenStacked: true,
      cell: (month) =>
        month.creditsUsedCents > 0 ? (
          <RevenueBar cents={month.creditsUsedCents} max={max} />
        ) : (
          <Text as="span" size="sm" tone="muted">
            No billed batches
          </Text>
        ),
    },
  ];
}

// ------------------------------------------------------------ Customer trends

function TrendChange({ row }: { row: InsightsCustomerTrend }) {
  if (row.shortBy) {
    return (
      <Text as="span" size="sm" tone="danger">
        Short {formatCount(row.shortBy)}
      </Text>
    );
  }
  const change = percentChange(row.delivered, row.priorDelivered);
  if (change === null) return <>{row.delivered > 0 ? "New" : "—"}</>;
  // Red is for problems: deliveries halving is one.
  return change <= -50 ? (
    <Text as="span" size="sm" tone="danger">
      {signed(change)}
    </Text>
  ) : (
    <>{signed(change)}</>
  );
}

const TREND_COLUMNS: DataColumn<InsightsCustomerTrend>[] = [
  {
    key: "customer",
    header: "Customer",
    cell: (row) => <Link to={routePath(row.customer.href)}>{row.customer.name}</Link>,
  },
  { key: "delivered", header: "Delivered", numeric: true, cell: (row) => formatCount(row.delivered) },
  { key: "change", header: "Change", numeric: true, cell: (row) => <TrendChange row={row} /> },
  {
    key: "reports",
    header: "Reports",
    numeric: true,
    cell: (row) =>
      row.reports > 0 ? (
        <Text as="span" size="sm" tone="danger">
          {formatCount(row.reports)}
        </Text>
      ) : (
        "0"
      ),
  },
];

// ---------------------------------------------------------- Source performance

export const SOURCE_COLUMNS: DataColumn<InsightsSourceRow>[] = [
  { key: "segment", header: "Segment", cell: (row) => segmentLabel(row) },
  {
    key: "niche",
    header: "Niche",
    cell: (row) => (row.niche ? `${row.niche}${row.nicheConfirmed ? "" : " (unconfirmed)"}` : "—"),
  },
  { key: "delivered", header: "Delivered", numeric: true, cell: (row) => formatCount(row.delivered) },
  { key: "positive", header: "Positive", numeric: true, cell: (row) => formatRate(row.positiveRate) },
  { key: "booked", header: "Booked", numeric: true, cell: (row) => formatRate(row.bookedRate) },
  { key: "poor", header: "Poor", numeric: true, cell: (row) => formatRate(row.poorRate) },
];

function outcomesNote(reported: number): string {
  return reported === 0
    ? "Rates stay empty until customers report outcomes. None reported yet."
    : `Rates stay empty until a segment has outcomes. ${formatCount(reported)} reported so far.`;
}

// ------------------------------------------------------------ Nightly reviews

function read(summary: Record<string, unknown>, key: string): Record<string, unknown> {
  const value = summary[key];
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

/** Reads a Nightly Review summary defensively: the in-process nightly
 *  attempt and the scheduled review write different shapes. */
export function reviewFacts(summary: Record<string, unknown>) {
  const segments = Array.isArray(summary["segments"]) ? summary["segments"] : null;
  const held = segments
    ? segments.filter((segment) => (segment as Record<string, unknown>)["anomalous"]).length
    : 0;
  const inventory = read(summary, "inventory");
  const failures = Array.isArray(summary["failures"])
    ? (summary["failures"] as { kind?: string; message?: string }[])
    : [];
  const configuration = read(summary, "configuration");
  return {
    configuration:
      configuration["version"] !== undefined ? `v${String(configuration["version"])}` : null,
    segments: segments ? segments.length : null,
    held,
    total: typeof inventory["total"] === "number" ? inventory["total"] : null,
    // `eligible` is the stored usable DB1 count; reviews written before the
    // background recount existed carry none, which must not read as zero.
    usable: typeof inventory["eligible"] === "number" ? inventory["eligible"] : null,
    failures,
  };
}

const REVIEW_STATUS: Record<string, { label: string; tone: StatusTone }> = {
  complete: { label: "Complete", tone: "success" },
  attention: { label: "Needs attention", tone: "danger" },
  waiting_publication: { label: "Waiting to publish", tone: "danger" },
  missed: { label: "Missed", tone: "danger" },
};

const TELEGRAM_STATUS: Record<string, { label: string; tone: StatusTone }> = {
  sent: { label: "Sent", tone: "success" },
  pending: { label: "Sending", tone: "info" },
  sending: { label: "Sending", tone: "info" },
  unknown: { label: "Unknown", tone: "danger" },
};

function words(value: string): string {
  const text = value.replaceAll("_", " ").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "Unknown";
}

function reviewColumns(now: Date): DataColumn<NightlyReviewRow>[] {
  return [
    {
      key: "night",
      header: "Night",
      cell: (row) => {
        const label = formatDay(row.reviewDate ? `${row.reviewDate}T12:00:00` : row.createdAt, now);
        return row.scraperRunId !== null ? (
          <Link to={`/admin/acquisition/runs/${row.scraperRunId}`}>{label}</Link>
        ) : (
          label
        );
      },
    },
    {
      key: "status",
      header: "Status",
      cell: (row) => {
        const status = REVIEW_STATUS[row.status] ?? { label: words(row.status), tone: "neutral" as const };
        return <StatusBadge tone={status.tone}>{status.label}</StatusBadge>;
      },
    },
    {
      key: "segments",
      header: "Segments",
      numeric: true,
      cell: (row) => {
        const facts = reviewFacts(row.summary);
        if (facts.segments === null) return "—";
        return facts.held ? (
          <>
            {formatCount(facts.segments)} ·{" "}
            <Text as="span" size="sm" tone="danger">
              {formatCount(facts.held)} held
            </Text>
          </>
        ) : (
          formatCount(facts.segments)
        );
      },
    },
    {
      key: "inventory",
      header: "Inventory",
      numeric: true,
      cell: (row) => {
        const total = reviewFacts(row.summary).total;
        return total === null ? "—" : formatCount(total);
      },
    },
    {
      key: "usable",
      header: "DB1 usable",
      numeric: true,
      cell: (row) => {
        const usable = reviewFacts(row.summary).usable;
        return usable === null ? "Not recorded" : formatCount(usable);
      },
    },
    {
      key: "telegram",
      header: "Telegram",
      cell: (row) => {
        const status = TELEGRAM_STATUS[row.telegramDeliveryState] ?? {
          label: words(row.telegramDeliveryState),
          tone: "neutral" as const,
        };
        return <StatusBadge tone={status.tone}>{status.label}</StatusBadge>;
      },
    },
    {
      key: "problems",
      header: "Problems",
      cell: (row) => {
        const failures = reviewFacts(row.summary).failures;
        const problems = [
          ...failures.map(
            (failure) => `${words(failure.kind ?? "failure")}${failure.message ? `: ${failure.message}` : ""}`,
          ),
          ...(row.telegramDeliveryError ? [row.telegramDeliveryError] : []),
          ...(row.reconcilable ? ["Telegram delivery needs reconciling"] : []),
        ];
        return problems.length ? (
          <Text as="span" size="sm" tone="danger">
            {problems.join(" · ")}
          </Text>
        ) : (
          "None"
        );
      },
    },
  ];
}

// ---------------------------------------------------------------------- Route

export function AdminInsightsRoute() {
  const data = useLoaderData<InsightsData>();
  const now = new Date();
  useDocumentTitle("Insights");
  const days = data.windowDays;
  const { creditsUsed, creditsBought, delivered, leadReports, sourcePerformance } = data;
  const sourceWindow = sourcePerformance.windowDays
    ? ` · trailing ${sourcePerformance.windowDays} days`
    : "";

  return (
    <Page
      title="Insights"
      width="data"
      density="data"
      description="Weekly review: revenue, customer trends, and which sources produce good leads."
    >
      <KpiRow label="Last 30 days">
        <Kpi
          label={`Credits used · ${days} days`}
          value={formatCents(creditsUsed.cents)}
          meta={changeLine(creditsUsed.cents, creditsUsed.priorCents, days, "None used")}
        />
        <Kpi
          label={`Credits bought · ${days} days`}
          value={formatCents(creditsBought.cents)}
          meta={plural(creditsBought.purchases, "purchase")}
        />
        <Kpi
          label={`Delivered · ${days} days`}
          value={formatCount(delivered.leads)}
          meta={changeLine(delivered.leads, delivered.priorLeads, days, "None delivered")}
        />
        <Kpi
          label={`Lead reports · ${days} days`}
          value={formatCount(leadReports.count)}
          meta={changeLine(leadReports.count, leadReports.priorCount, days, "None")}
        />
      </KpiRow>

      <div className="jx-insights-halves">
        <Section title="Revenue by month">
          <DataTable
            label="Revenue by month"
            columns={revenueColumns(data.revenueByMonth, now)}
            rows={data.revenueByMonth}
            rowKey={(month) => month.month}
            empty="No batch has been charged or delivered yet."
          />
        </Section>
        <Section title={`Customer trends · ${days} days`}>
          <DataTable
            label="Customer trends"
            columns={TREND_COLUMNS}
            rows={data.customerTrends}
            rowKey={(row) => String(row.customer.id)}
            empty={`No customer has received leads in the last ${days * 2} days.`}
          />
        </Section>
      </div>

      <Section
        title={`Source performance${sourceWindow}`}
        description={outcomesNote(sourcePerformance.outcomesReported)}
      >
        <DataTable
          label="Source performance"
          columns={SOURCE_COLUMNS}
          rows={sourcePerformance.rows}
          rowKey={(row) => row.segment}
          empty="No nightly Source Performance snapshot has been recorded yet."
        />
        <div>
          <ActionLink href="/app/admin/insights/sources">
            {sourcePerformance.segments
              ? `All ${formatCount(sourcePerformance.segments)} segments`
              : "Source performance history"}
          </ActionLink>
        </div>
      </Section>

      <Section
        id="nightly-reviews"
        title="Nightly reviews"
        description="Each night's run and whether Telegram delivered its summary."
      >
        <DataTable
          label="Nightly reviews"
          columns={reviewColumns(now)}
          rows={data.nightlyReviews}
          rowKey={(row) => row.id}
          empty="A review is written for each nightly run."
        />
      </Section>
    </Page>
  );
}
