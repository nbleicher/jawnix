import { useEffect, useRef, useState } from "react";
import type { DragEvent, FormEvent } from "react";
import { useLoaderData, useRevalidator } from "react-router";

import { ActionLink, Button } from "../../design-system/primitives/Button";
import { Field, Input, Select } from "../../design-system/primitives/form";
import { Page, Section, Stack } from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import type { StatusTone } from "../../design-system/primitives/status";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { Text } from "../../design-system/primitives/typography";
import { api } from "../api";
import { formatCount, formatDatabase, formatDateTime } from "../operator/format";
import { LEAD_UPLOADS as UPLOADS, submitLeadUpload } from "../operator/leadUploads";
import type { LeadUploadStatus } from "../operator/leadUploads";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { BackToInventory } from "./AdminInventory";
import "./AdminInventory.css";

/**
 * Lead uploads, moved from /admin/lead-uploads into Inventory.
 *
 * Every upload adds to one DB2 or DB3 inventory, so this lists them all,
 * including those the retired managed administrators made. A DB3 upload
 * carries the date its rows were scrubbed; the rows sell for 30 days after
 * it. Imports run in the background; rows still importing are re-read every
 * few seconds until they settle.
 */

export interface UploadWorkspace {
  uploads: LeadUploadStatus[];
  states: { code: string; name: string }[];
  inventory: Record<"db2" | "db3", { total: number; states: Record<string, number>; asOf: string | null }>;
}

const POLL_MS = 5_000;

const STATUS: Record<LeadUploadStatus["status"], { label: string; tone: StatusTone }> = {
  uploading: { label: "Uploading", tone: "info" },
  queued: { label: "Queued", tone: "info" },
  processing: { label: "Importing", tone: "info" },
  complete: { label: "Ready", tone: "success" },
  failed: { label: "Failed", tone: "danger" },
};

function importing(item: LeadUploadStatus): boolean {
  return item.status === "queued" || item.status === "processing";
}

export async function adminInventoryUploadsLoader(): Promise<UploadWorkspace> {
  return api<UploadWorkspace>(UPLOADS);
}

function UploadForm({
  states,
  onUploaded,
}: {
  states: UploadWorkspace["states"];
  onUploaded: (item: LeadUploadStatus) => void;
}) {
  const revalidator = useRevalidator();
  const [file, setFile] = useState<File | null>(null);
  const [state, setState] = useState("");
  const [database, setDatabase] = useState<"db2" | "db3">("db2");
  const [scrubbedOn, setScrubbedOn] = useState(() => new Date().toISOString().slice(0, 10));
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const picker = useRef<HTMLInputElement>(null);

  function choose(selected: File | null) {
    if (busy) return;
    if (selected && !selected.name.toLowerCase().endsWith(".csv")) {
      setError("Choose a CSV file.");
      return;
    }
    if (picker.current) {
      if (selected) {
        const transfer = new DataTransfer();
        transfer.items.add(selected);
        picker.current.files = transfer.files;
      } else picker.current.value = "";
    }
    setFile(selected);
    setError("");
  }

  function drop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    if (event.dataTransfer.files.length === 1) choose(event.dataTransfer.files[0] ?? null);
    else setError("Choose one CSV file per upload.");
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || !state || busy) return;
    setBusy(true);
    setProgress(0);
    setError("");
    try {
      onUploaded(await submitLeadUpload(file, state, setProgress, {
        database,
        ...(database === "db3" && scrubbedOn ? { scrubbedOn } : {}),
      }));
      setFile(null);
      if (picker.current) picker.current.value = "";
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The upload could not be completed.");
      if ([401, 403].includes((caught as { status?: number }).status ?? 0)) void revalidator.revalidate();
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={(event) => void submit(event)} aria-label="New lead upload">
      <Stack gap={4}>
        <div className="jx-upload-form">
          <div
            className="jx-upload-drop"
            data-dragging={dragging || undefined}
            onDragOver={(event) => {
              event.preventDefault();
              if (!busy) setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={drop}
          >
            <Text size="sm" weight="semibold">
              Drop a CSV file here, or choose one
            </Text>
            <Field
              label="CSV file"
              required
              description="Needs a Phone or Phone Number column. Name or Business Name is optional."
            >
              <Input
                ref={picker}
                type="file"
                accept=".csv,text/csv"
                disabled={busy}
                onChange={(event) => choose(event.target.files?.[0] ?? null)}
              />
            </Field>
            {file ? (
              <Text size="sm">
                {file.name} · {(file.size / 1024 / 1024).toFixed(1)} MB
              </Text>
            ) : null}
          </div>
          <Stack gap={4}>
            <Field label="Database" required description="DB3 holds prescrubbed rows that sell for 30 days after the scrub date.">
              <Select
                value={database}
                onChange={(event) => setDatabase(event.target.value as "db2" | "db3")}
                disabled={busy}
              >
                <option value="db2">DB2 · Standard uploads</option>
                <option value="db3">DB3 · Clean uploads</option>
              </Select>
            </Field>
            {database === "db3" ? (
              <Field label="Scrubbed on" description="The fallback date for rows without a Scrubbed On column. Not in the future, not older than 30 days.">
                <Input
                  type="date"
                  value={scrubbedOn}
                  onChange={(event) => setScrubbedOn(event.target.value)}
                  disabled={busy}
                />
              </Field>
            ) : null}
            <Field label="State" required description="Every row in the file is filed under this state.">
              <Select value={state} onChange={(event) => setState(event.target.value)} disabled={busy}>
                <option value="">Choose a state</option>
                {states.map((item) => (
                  <option key={item.code} value={item.code}>
                    {item.name} ({item.code})
                  </option>
                ))}
              </Select>
            </Field>
            <div>
              <Button type="submit" variant="primary" busy={busy} busyLabel="Uploading…" disabled={!file || !state}>
                Upload CSV
              </Button>
            </div>
          </Stack>
        </div>
        {error ? (
          <Text role="alert" tone="danger">
            {error}
          </Text>
        ) : null}
        {busy ? (
          <Stack gap={2}>
            <Text role="status">Uploading · {progress}%</Text>
            <progress aria-label="File upload progress" max={100} value={progress} />
          </Stack>
        ) : null}
        <Text size="sm" tone="muted">
          Large files import in the background. Leads become usable once the whole file is processed,
          and the counts on Inventory update after the next recount.
        </Text>
      </Stack>
    </form>
  );
}

export function AdminInventoryUploadsRoute() {
  const initial = useLoaderData<UploadWorkspace>();
  const revalidator = useRevalidator();
  const revalidate = revalidator.revalidate;
  const [uploads, setUploads] = useState(initial.uploads);
  const [error, setError] = useState("");
  const revisions = useRef<Record<string, number>>({});
  useDocumentTitle("Lead uploads");

  useEffect(() => {
    setUploads((current) => {
      const known = new Set(initial.uploads.map((item) => item.id));
      return [...current.filter((item) => !known.has(item.id)), ...initial.uploads].slice(0, 50);
    });
  }, [initial]);

  const scope = uploads.filter(importing).map((item) => item.id).sort().join(",");
  useEffect(() => {
    if (!scope) return;
    let mounted = true;
    let inFlight = false;
    const ids = scope.split(",");
    async function refresh() {
      if (inFlight || document.visibilityState === "hidden") return;
      inFlight = true;
      const versions = ids.map((id) => revisions.current[id] ?? 0);
      try {
        const changed = await Promise.all(ids.map((id) => api<LeadUploadStatus>(`${UPLOADS}/${id}`)));
        if (!mounted) return;
        setUploads((current) =>
          current.map((item) => {
            const index = changed.findIndex((row) => row.id === item.id);
            return index >= 0 && (revisions.current[item.id] ?? 0) === versions[index] ? changed[index]! : item;
          }),
        );
        if (changed.some((item) => item.status === "complete")) void revalidate();
      } catch (caught) {
        const status = (caught as { status?: number }).status;
        if (status === 401 || status === 403) void revalidate();
      } finally {
        inFlight = false;
      }
    }
    const interval = window.setInterval(() => void refresh(), POLL_MS);
    return () => {
      mounted = false;
      window.clearInterval(interval);
    };
  }, [scope, revalidate]);

  async function retry(id: string) {
    setError("");
    revisions.current[id] = (revisions.current[id] ?? 0) + 1;
    try {
      const item = await api<LeadUploadStatus>(`${UPLOADS}/${id}/retry`, { method: "POST" });
      setUploads((current) => current.map((row) => (row.id === id ? item : row)));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Retry was not accepted.");
    }
  }

  const columns: DataColumn<LeadUploadStatus>[] = [
    {
      key: "file",
      header: "File",
      cell: (row) => (
        <Stack gap={1}>
          <span>{row.filename}</span>
          {row.error ? (
            <Text as="span" size="xs" tone="danger">
              {row.error}
            </Text>
          ) : null}
        </Stack>
      ),
    },
    { key: "database", header: "DB", cell: (row) => formatDatabase(row.database) },
    {
      key: "scrubbed",
      header: "Scrubbed",
      cell: (row) => (row.scrubbedOn ? row.scrubbedOn : "—"),
    },
    { key: "state", header: "State", cell: (row) => row.state },
    { key: "by", header: "Uploaded by", cell: (row) => row.uploadedBy ?? "Operator" },
    {
      key: "status",
      header: "Status",
      cell: (row) => <StatusBadge tone={STATUS[row.status].tone}>{STATUS[row.status].label}</StatusBadge>,
    },
    { key: "rows", header: "Rows", numeric: true, cell: (row) => formatCount(row.processedRows) },
    {
      key: "added",
      header: "Added",
      numeric: true,
      cell: (row) => (row.status === "complete" ? formatCount(row.importedRows) : "—"),
    },
    {
      key: "updated",
      header: "Updated",
      numeric: true,
      cell: (row) => (row.status === "complete" ? formatCount(row.updatedRows) : "—"),
    },
    { key: "rejected", header: "Rejected", numeric: true, cell: (row) => formatCount(row.rejectedRows) },
    { key: "uploaded", header: "Uploaded", cell: (row) => formatDateTime(row.createdAt) },
    {
      key: "action",
      header: "Action",
      cell: (row) =>
        row.status === "failed" ? (
          <Button variant="secondary" onClick={() => void retry(row.id)}>
            Retry import
          </Button>
        ) : row.rejectedRows > 0 && row.status === "complete" ? (
          <ActionLink href={`${UPLOADS}/${row.id}/rejected`} download>
            Download rejected rows
          </ActionLink>
        ) : null,
    },
  ];

  return (
    <Page
      title="Lead uploads"
      width="data"
      density="data"
      description="Add CSV leads to DB2, or prescrubbed leads to DB3. Every upload adds to one inventory."
    >
      <Section title="New upload">
        <UploadForm
          states={initial.states}
          onUploaded={(item) => setUploads((current) => [item, ...current])}
        />
      </Section>
      <Section
        title="Uploads"
        description={`${formatCount(initial.inventory.db2.total)} phone numbers in DB2 and ${formatCount(initial.inventory.db3.total)} in DB3 across every upload.`}
      >
        {error ? (
          <Text role="alert" tone="danger">
            {error}
          </Text>
        ) : null}
        <DataTable
          label="Lead uploads"
          columns={columns}
          rows={uploads}
          rowKey={(row) => row.id}
          hasActions
          empty="No uploads yet. Upload a CSV to start building DB2 or DB3."
        />
      </Section>
      <BackToInventory />
    </Page>
  );
}
