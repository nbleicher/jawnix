import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { Button } from "../../design-system/primitives/Button";
import { Cluster, Section, Stack } from "../../design-system/primitives/layout";
import { PanelLayout } from "../../design-system/primitives/panel";
import { StatusBadge } from "../../design-system/primitives/status";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { Text } from "../../design-system/primitives/typography";
import { api } from "../api";
import { routePath } from "../routePath";
import { BatchPanel } from "../operator/BatchPanel";
import {
  formatCount,
  formatDay,
  formatStates,
  stageLabel,
  stageTone,
} from "../operator/format";
import type { BatchRow } from "../operator/format";
import { useSelectedRow } from "../operator/useSelectedRow";
import { formatUsdCents } from "./AdminCustomerControls";

/**
 * The Customer page's Batches and Reports tabs.
 *
 * Batches reads one Customer's batches from All batches' own endpoint,
 * newest first and paged, with the selected batch open beside the list.
 */

interface CustomerBatchPage {
  items: BatchRow[];
  page: number;
  pageSize: number;
  total: number;
  pages: number;
}

/** A pending conflict is the decision on a batch that is otherwise only
 *  waiting or approved: name it rather than the stage beneath it. */
function inConflict(row: BatchRow): boolean {
  return row.needsDecision && !["awaiting_approval", "delivery_failed", "failed"].includes(row.stage);
}

function StatusCell({ row }: { row: BatchRow }) {
  if (inConflict(row)) return <StatusBadge tone="danger">Conflict</StatusBadge>;
  return <StatusBadge tone={stageTone(row.stage)}>{stageLabel(row.stage)}</StatusBadge>;
}

function costText(row: BatchRow): string {
  const cost = row.cost;
  if (!cost?.billed) return "Not billed";
  const amount = formatUsdCents(cost.amountCents ?? 0);
  return cost.hold === "active" ? `${amount} held` : amount;
}

export function CustomerBatchesTab({
  customerId,
  onChanged,
}: {
  customerId: number;
  /** Re-read the page's numbers after an action in the side panel. */
  onChanged: () => void;
}) {
  const [params, setParams] = useSearchParams();
  const selection = useSelectedRow("batch");
  const requested = Number(params.get("page") ?? "1");
  const page = Number.isInteger(requested) && requested > 0 ? requested : 1;
  const [data, setData] = useState<CustomerBatchPage | null>(null);
  const [error, setError] = useState("");
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let current = true;
    setError("");
    api<CustomerBatchPage>(
      `/api/admin/batches?customer=${customerId}&status=all&page=${page}`,
    )
      .then((value) => {
        if (current) setData(value);
      })
      .catch((caught: unknown) => {
        if (!current) return;
        setError(caught instanceof Error ? caught.message : "These batches could not be read.");
      });
    return () => {
      current = false;
    };
  }, [customerId, page, version]);

  const reload = useCallback(() => setVersion((value) => value + 1), []);

  function goTo(next: number) {
    setParams(
      (current) => {
        const value = new URLSearchParams(current);
        value.delete("batch");
        if (next <= 1) value.delete("page");
        else value.set("page", String(next));
        return value;
      },
      { preventScrollReset: true },
    );
  }

  const selectedNumber = selection.selected ? Number(selection.selected) : null;
  const rows = data?.items ?? [];
  const selectedRow = rows.find((row) => row.number === selectedNumber);

  const columns: DataColumn<BatchRow>[] = [
    {
      key: "batch",
      header: "Batch",
      cell: (row) => (
        <Link
          to={routePath(row.href)}
          data-panel-trigger={String(row.number)}
          onClick={selection.openFromLink(String(row.number))}
        >
          #{row.number}
        </Link>
      ),
    },
    { key: "leads", header: "Leads", numeric: true, cell: (row) => formatCount(row.leadCount) },
    { key: "states", header: "States", cell: (row) => formatStates(row.states) },
    { key: "status", header: "Status", cell: (row) => <StatusCell row={row} /> },
    { key: "submitted", header: "Submitted", cell: (row) => formatDay(row.createdAt) },
    { key: "cost", header: "Cost", numeric: true, cell: (row) => costText(row) },
  ];

  if (error && !data) {
    return (
      <Stack gap={3} align="flex-start">
        <Text role="alert" tone="danger" size="sm">
          {error}
        </Text>
        <Button onClick={reload}>Try again</Button>
      </Stack>
    );
  }

  return (
    <Section title="Batches" description="Every batch this customer has requested, newest first.">
      <PanelLayout
        panel={
          selectedNumber !== null && Number.isFinite(selectedNumber) ? (
            <BatchPanel
              key={selectedNumber}
              reference={selectedNumber}
              context="customer"
              fallbackTitle={
                selectedRow
                  ? `#${selectedRow.number} · ${formatCount(selectedRow.leadCount)} leads`
                  : `Batch #${selectedNumber}`
              }
              onClose={selection.close}
              onChanged={() => {
                reload();
                onChanged();
              }}
              autoFocus={selection.autoFocus}
              returnFocus={selection.returnFocus(String(selectedNumber))}
            />
          ) : null
        }
      >
        {data ? (
          <DataTable
            label="Batches"
            columns={columns}
            rows={rows}
            rowKey={(row) => row.id}
            selectedKey={selectedRow?.id ?? null}
            onRowClick={(row) => selection.select(String(row.number))}
            empty="This customer has not requested a batch yet."
          />
        ) : (
          <Text size="sm" tone="muted" role="status">
            Reading batches…
          </Text>
        )}
        {data && data.pages > 1 ? (
          <Cluster gap={3} justify="space-between">
            <Text size="sm" tone="muted" role="status">
              Page {data.page} of {data.pages} · {formatCount(data.total)} batches
            </Text>
            <nav aria-label="Batch pages">
              <Cluster gap={3}>
                {data.page > 1 ? <Button onClick={() => goTo(data.page - 1)}>Previous</Button> : null}
                {data.page < data.pages ? <Button onClick={() => goTo(data.page + 1)}>Next</Button> : null}
              </Cluster>
            </nav>
          </Cluster>
        ) : null}
      </PanelLayout>
    </Section>
  );
}

export interface CustomerLeadReportView {
  id: string;
  href: string;
  reason: string;
  reasonLabel: string;
  details: string;
  status: string;
  statusLabel: string;
  createdAt: string;
  lead: { phone: string; title: string; state: string } | null;
  batch: { number: number; href: string } | null;
  eligibilityHeld: boolean;
}

function formatPhone(phone: string): string {
  const digits = phone.replace(/\D/g, "");
  return digits.length === 10
    ? `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}`
    : phone;
}

const REPORT_COLUMNS: DataColumn<CustomerLeadReportView>[] = [
  {
    key: "reported",
    header: "Reported",
    cell: (report) => <Link to={routePath(report.href)}>{formatDay(report.createdAt)}</Link>,
  },
  {
    key: "lead",
    header: "Lead",
    cell: (report) =>
      report.lead ? `${formatPhone(report.lead.phone)} · ${report.lead.title || "Untitled"}` : "—",
  },
  {
    key: "batch",
    header: "Batch",
    cell: (report) =>
      report.batch ? <Link to={routePath(report.batch.href)}>#{report.batch.number}</Link> : "—",
  },
  { key: "reason", header: "Reason", cell: (report) => report.reasonLabel },
  {
    key: "status",
    header: "Status",
    cell: (report) => (
      <StatusBadge tone={report.status === "open" ? "danger" : "neutral"}>{report.statusLabel}</StatusBadge>
    ),
  },
  {
    key: "hold",
    header: "Hold",
    cell: (report) => (report.eligibilityHeld ? "Held from allocation" : "—"),
  },
];

export function CustomerReportsTab({ reports }: { reports: CustomerLeadReportView[] }) {
  return (
    <Section
      title="Lead reports"
      description="Leads this customer reported as bad, newest first. Each opens with its evidence and decisions."
    >
      <DataTable
        label="Lead reports"
        columns={REPORT_COLUMNS}
        rows={reports}
        rowKey={(report) => report.id}
        empty="This customer has never reported a lead."
      />
    </Section>
  );
}
