import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import type { LoaderFunctionArgs } from "react-router";
import { Link, useLoaderData, useRevalidator } from "react-router";

import { Button } from "../../design-system/primitives/Button";
import { Field, Input, Select } from "../../design-system/primitives/form";
import { Page, Section, Stack } from "../../design-system/primitives/layout";
import { PanelLayout } from "../../design-system/primitives/panel";
import { StatusBadge } from "../../design-system/primitives/status";
import type { StatusTone } from "../../design-system/primitives/status";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { Text } from "../../design-system/primitives/typography";
import { api } from "../api";
import { errorMessage } from "../operator/actions";
import { DecisionPanel } from "../operator/DecisionPanel";
import type { Decision } from "../operator/DecisionPanel";
import { formatAge, formatCount, formatDateTime } from "../operator/format";
import { useSelectedRow } from "../operator/useSelectedRow";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { BackToInventory, Pager } from "./AdminInventory";
import type { Paged } from "./AdminInventory";
import { EXCLUSION_TYPES, INGESTING_STATUSES, exclusionTypeLabel } from "./exclusionLists";
import type { ExclusionListStatus } from "./exclusionLists";
import "./AdminInventory.css";

/**
 * Exclusion Lists, moved from Acquisition into Inventory: Customer uploads
 * waiting for a global decision (confirmed or denied in the same side panel
 * Today uses), the operator's bulk upload, and every list so far.
 */

export interface ExclusionRow {
  id: string;
  customer: { id: number; name: string; href: string } | null;
  customerName: string;
  type: string;
  filename: string;
  status: string;
  global: boolean;
  acceptedRows: number;
  invalidRows: number;
  duplicateRows: number;
  poolImpact: number;
  error: string;
  decisionReason: string;
  createdAt: string;
  decidedAt: string | null;
}

export interface ExclusionListsData {
  waiting: ExclusionRow[];
  lists: Paged<ExclusionRow>;
}

/** Red needs a decision or failed; black is in force; grey is scoped to one
 *  Customer (V4). */
const STATUSES: Record<string, { label: string; tone: StatusTone }> = {
  queued: { label: "Processing", tone: "info" },
  ingesting: { label: "Processing", tone: "info" },
  pending_confirmation: { label: "Waiting", tone: "danger" },
  confirmed: { label: "Global", tone: "success" },
  denied: { label: "Customer only", tone: "neutral" },
  failed: { label: "Failed", tone: "danger" },
};

function status(value: string) {
  return STATUSES[value] ?? { label: value.replaceAll("_", " "), tone: "neutral" as StatusTone };
}

export async function adminInventoryExclusionsLoader({
  request,
}: LoaderFunctionArgs): Promise<ExclusionListsData> {
  const page = new URL(request.url).searchParams.get("page");
  return api<ExclusionListsData>(
    `/api/admin/exclusion-lists${page && /^\d+$/.test(page) ? `?page=${page}` : ""}`,
  );
}

/** The same Decision Today builds for a waiting list, so both open one panel. */
function asDecision(row: ExclusionRow): Decision {
  return {
    key: `exclusionList:${row.id}`,
    kind: "exclusionList",
    label: "Exclusion list",
    recordId: row.id,
    subject: row.customerName,
    detail: exclusionTypeLabel(row.type),
    since: row.createdAt,
    href: null,
    batch: null,
    facts: {
      customerName: row.customerName,
      typeLabel: exclusionTypeLabel(row.type),
      filename: row.filename,
      acceptedRows: row.acceptedRows,
      invalidRows: row.invalidRows,
      duplicateRows: row.duplicateRows,
      poolImpact: row.poolImpact,
      createdAt: row.createdAt,
    },
  };
}

/** Operator bulk upload (#153 story 25): global as soon as it is processed,
 *  with no Nightly Review gate, so it demands an audit reason. */
function BulkUpload() {
  const revalidator = useRevalidator();
  const [file, setFile] = useState<File | null>(null);
  const [type, setType] = useState<string>(EXCLUSION_TYPES[0].value);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const [upload, setUpload] = useState<ExclusionListStatus | null>(null);
  const [pollAttempt, setPollAttempt] = useState(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const ingesting = upload !== null && INGESTING_STATUSES.includes(upload.status);

  useEffect(() => {
    if (!ingesting || upload === null) return;
    const timer = window.setTimeout(() => {
      void api<ExclusionListStatus>(`/api/admin/exclusion-lists/${upload.id}`)
        .then((next) => {
          setUpload(next);
          if (!INGESTING_STATUSES.includes(next.status)) void revalidator.revalidate();
        })
        .catch(() => setPollAttempt((attempt) => attempt + 1));
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [ingesting, upload, pollAttempt, revalidator]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      setFailure("Choose a CSV file to upload.");
      return;
    }
    if (!reason.trim()) {
      setFailure("An upload reason is required.");
      return;
    }
    setBusy(true);
    setFailure("");
    const body = new FormData();
    body.append("file", file);
    body.append("type", type);
    body.append("reason", reason.trim());
    try {
      setUpload(await api<ExclusionListStatus>("/api/admin/exclusion-lists", { method: "POST", body }));
      setFile(null);
      setReason("");
      if (fileInput.current) fileInput.current.value = "";
      void revalidator.revalidate();
    } catch (caught) {
      setFailure(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={(event) => void submit(event)} noValidate aria-label="Bulk upload">
      <Stack gap={4}>
        <Text size="sm" tone="muted">
          Every accepted phone becomes ineligible for every Customer as soon as processing finishes,
          with no Nightly Review confirmation. CSV with a phone column, 1,000–50,000 rows.
        </Text>
        <div className="jx-upload-form">
          <Field label="CSV file" required>
            <Input
              ref={fileInput}
              type="file"
              accept=".csv,text/csv"
              onChange={(event) => setFile(event.currentTarget.files?.[0] ?? null)}
            />
          </Field>
          <Field label="Type" required>
            <Select value={type} onChange={(event) => setType(event.currentTarget.value)}>
              {EXCLUSION_TYPES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <Field label="Reason" required description="Recorded in Activity as the reason for the global effect.">
          <Input value={reason} onChange={(event) => setReason(event.currentTarget.value)} />
        </Field>
        <div>
          <Button type="submit" variant="primary" busy={busy} busyLabel="Uploading…">
            Upload globally
          </Button>
        </div>
        <Text size="sm" role="status">
          {upload
            ? upload.status === "failed"
              ? `Failed: ${upload.error || "the file could not be processed."}`
              : INGESTING_STATUSES.includes(upload.status)
                ? `Processing ${upload.filename}…`
                : `${formatCount(upload.acceptedRows)} phones from ${upload.filename} are now excluded for every Customer.`
            : ""}
        </Text>
        {failure ? (
          <Text size="sm" tone="danger" role="alert">
            {failure}
          </Text>
        ) : null}
      </Stack>
    </form>
  );
}

function customerCell(row: ExclusionRow) {
  return row.customer ? <Link to={`/admin/customers/${row.customer.id}`}>{row.customer.name}</Link> : row.customerName;
}

export function AdminInventoryExclusionsRoute() {
  const data = useLoaderData<ExclusionListsData>();
  const revalidator = useRevalidator();
  const selection = useSelectedRow("list");
  useDocumentTitle("Exclusion lists");

  const selected = data.waiting.find((row) => row.id === selection.selected) ?? null;
  // A list decided elsewhere leaves `waiting`; keep its panel open, settled.
  const [held, setHeld] = useState<ExclusionRow | null>(null);
  useEffect(() => {
    if (selected) setHeld(selected);
    else if (!selection.selected) setHeld(null);
  }, [selected, selection.selected]);
  const open = selected ?? (held && held.id === selection.selected ? held : null);

  const waitingColumns: DataColumn<ExclusionRow>[] = [
    {
      key: "customer",
      header: "Customer",
      cell: (row) => (
        <Link
          to={`/admin/inventory/exclusions?list=${row.id}`}
          data-panel-trigger={row.id}
          onClick={selection.openFromLink(row.id)}
        >
          {row.customerName}
        </Link>
      ),
    },
    { key: "type", header: "Type", cell: (row) => exclusionTypeLabel(row.type) },
    { key: "file", header: "File", cell: (row) => row.filename },
    { key: "accepted", header: "Accepted", numeric: true, cell: (row) => formatCount(row.acceptedRows) },
    { key: "pool", header: "In pool", numeric: true, cell: (row) => formatCount(row.poolImpact) },
    { key: "waiting", header: "Waiting", cell: (row) => formatAge(row.createdAt) },
  ];

  const listColumns: DataColumn<ExclusionRow>[] = [
    { key: "file", header: "File", cell: (row) => row.filename },
    { key: "customer", header: "Uploaded by", cell: customerCell },
    { key: "type", header: "Type", cell: (row) => exclusionTypeLabel(row.type) },
    {
      key: "status",
      header: "Status",
      cell: (row) => <StatusBadge tone={status(row.status).tone}>{status(row.status).label}</StatusBadge>,
    },
    { key: "accepted", header: "Accepted", numeric: true, cell: (row) => formatCount(row.acceptedRows) },
    { key: "pool", header: "In pool", numeric: true, cell: (row) => formatCount(row.poolImpact) },
    { key: "uploaded", header: "Uploaded", cell: (row) => formatDateTime(row.createdAt) },
  ];

  return (
    <Page
      title="Exclusion lists"
      width="data"
      density="data"
      description="Phones no Customer receives. Confirm Customer lists globally, or upload one for everyone."
    >
      <PanelLayout
        panel={
          open ? (
            <DecisionPanel
              key={open.id}
              decision={asDecision(open)}
              settled={!selected}
              onClose={selection.close}
              onChanged={() => void revalidator.revalidate()}
              autoFocus={selection.autoFocus}
              returnFocus={selection.returnFocus(open.id)}
            />
          ) : null
        }
      >
        <Section title="Waiting for a decision" description="Customer lists that would also apply to every Customer once confirmed.">
          <DataTable
            label="Waiting for a decision"
            columns={waitingColumns}
            rows={data.waiting}
            rowKey={(row) => row.id}
            selectedKey={open?.id ?? null}
            onRowClick={(row) => selection.select(row.id)}
            empty="No Customer list is waiting. New uploads arrive here after processing."
          />
        </Section>
        <Section title="Bulk upload">
          <BulkUpload />
        </Section>
        <Section title="All lists">
          <DataTable
            label="All exclusion lists"
            columns={listColumns}
            rows={data.lists.items}
            rowKey={(row) => row.id}
            empty="No Exclusion Lists yet."
          />
          <Pager path="/admin/inventory/exclusions" data={data.lists} noun="exclusion lists" />
        </Section>
      </PanelLayout>
      <BackToInventory />
    </Page>
  );
}
